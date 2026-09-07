"""Specification validation and deterministic planning helpers."""

__all__ = [
    "PreparedArchitectureInputs", "build_planning_index", "build_test_manifest_metadata",
    "compile_delivery_constraints", "prepare_architecture_inputs", "validate_architecture",
    "compile_delivery_blueprint", "link_plan", "normalize_plan_draft", "plan_lint", "derive_task_metadata", "interface_signature_digest", "blueprint_task_semantic_projection",
    "classify_migration", "project_plan_state", "project_file_ledger", "validate_revision_ledger", "validate_file_ledger", "validate_plan_successor", "successor_pointer", "build_revision_entry", "append_lease_finished", "append_lease_started", "append_revision_entry", "append_verification_committed", "latest_activation", "build_event_entry", "append_epoch_materialized", "initialize_plan_state",
    "plan_state_snapshot_lint", "project_state_transition", "validate_lease_authorization", "validate_state_transition", "execution_state_lint",
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
    if name in {"link_plan", "normalize_plan_draft", "plan_lint", "derive_task_metadata", "interface_signature_digest", "blueprint_task_semantic_projection"}:
        from .plan import blueprint_task_semantic_projection, derive_task_metadata, interface_signature_digest, link_plan, normalize_plan_draft, plan_lint
        return {"link_plan": link_plan, "normalize_plan_draft": normalize_plan_draft, "plan_lint": plan_lint, "derive_task_metadata": derive_task_metadata, "interface_signature_digest": interface_signature_digest, "blueprint_task_semantic_projection": blueprint_task_semantic_projection}[name]
    if name in {"classify_migration", "project_plan_state", "project_file_ledger", "validate_revision_ledger", "validate_file_ledger", "validate_plan_successor", "successor_pointer", "build_revision_entry", "append_lease_finished", "append_lease_started", "append_revision_entry", "append_verification_committed", "latest_activation", "build_event_entry", "append_epoch_materialized"}:
        from .plan_revision import append_epoch_materialized, append_lease_finished, append_lease_started, append_revision_entry, append_verification_committed, build_event_entry, build_revision_entry, classify_migration, latest_activation, project_file_ledger, project_plan_state, successor_pointer, validate_file_ledger, validate_plan_successor, validate_revision_ledger
        return {"classify_migration": classify_migration, "project_plan_state": project_plan_state, "project_file_ledger": project_file_ledger, "validate_revision_ledger": validate_revision_ledger, "validate_file_ledger": validate_file_ledger, "validate_plan_successor": validate_plan_successor, "successor_pointer": successor_pointer, "build_revision_entry": build_revision_entry, "append_lease_finished": append_lease_finished, "append_lease_started": append_lease_started, "append_revision_entry": append_revision_entry, "append_verification_committed": append_verification_committed, "latest_activation": latest_activation, "build_event_entry": build_event_entry, "append_epoch_materialized": append_epoch_materialized}[name]
    if name in {"initialize_plan_state", "plan_state_snapshot_lint", "project_state_transition", "validate_lease_authorization", "validate_state_transition", "execution_state_lint"}:
        from .plan_state import execution_state_lint, initialize_plan_state, plan_state_snapshot_lint, project_state_transition, validate_lease_authorization, validate_state_transition
        return {
            "initialize_plan_state": initialize_plan_state,
            "plan_state_snapshot_lint": plan_state_snapshot_lint,
            "project_state_transition": project_state_transition,
            "validate_lease_authorization": validate_lease_authorization,
            "validate_state_transition": validate_state_transition,
            "execution_state_lint": execution_state_lint,
        }[name]
    if name == "validate_architecture":
        from .architecture import validate_architecture
        return validate_architecture
    raise AttributeError(name)
