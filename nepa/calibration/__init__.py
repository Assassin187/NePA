"""Isolated calibration workflows."""

from .s4_architecture import (
    ArchitectureCalibrationDriver,
    ArchitecturePlannerContractBinding,
    CalibrationBatchDeclaration,
    CalibrationDeclarationError,
    CalibrationEvidenceError,
    CalibrationError,
    CalibrationModelTarget,
    DESIGN_BASELINE,
    bind_architecture_planner_contract,
    build_lineage_manifest,
    recompute_calibration_report,
    verify_design_baseline,
)

_PROMPT_DEVELOPMENT_EXPORTS = {
    "CONFIG_ENV", "CONTEXT_LIMITS_ENV", "CalibrationPreflight", "PromptDevelopmentConfigError",
    "PromptDevelopmentCoordinator", "PromptDevelopmentError", "PromptDevelopmentEvidenceError",
    "PromptSelectionTie", "PromptRecoveryCoordinator", "preflight_calibration_config", "scan_prompt_neutrality",
    "verify_recovery_authorization", "attest_predecessor_tie", "screen_recovery_report",
    "build_development_summary", "render_development_report", "validate_development_report", "write_development_report",
    "RECOVERY_AUTHORIZATION_ENV", "RECOVERY_CONFIG_ENV", "RECOVERY_CONTEXT_LIMITS_ENV",
}


def __getattr__(name: str):
    if name in _PROMPT_DEVELOPMENT_EXPORTS:
        from . import s4_prompt_development
        return getattr(s4_prompt_development, name)
    raise AttributeError(name)

__all__ = [
    "ArchitectureCalibrationDriver", "ArchitecturePlannerContractBinding", "CalibrationBatchDeclaration",
    "CalibrationDeclarationError", "CalibrationEvidenceError", "CalibrationError", "CalibrationModelTarget", "DESIGN_BASELINE",
    "bind_architecture_planner_contract", "build_lineage_manifest", "recompute_calibration_report", "verify_design_baseline",
    "CalibrationPreflight", "PromptDevelopmentConfigError", "PromptDevelopmentCoordinator", "PromptDevelopmentError",
    "PromptDevelopmentEvidenceError", "PromptSelectionTie", "preflight_calibration_config", "scan_prompt_neutrality",
    "CONFIG_ENV", "CONTEXT_LIMITS_ENV",
    "PromptRecoveryCoordinator", "verify_recovery_authorization", "attest_predecessor_tie", "screen_recovery_report",
    "build_development_summary", "render_development_report", "validate_development_report", "write_development_report",
    "RECOVERY_AUTHORIZATION_ENV", "RECOVERY_CONFIG_ENV", "RECOVERY_CONTEXT_LIMITS_ENV",
]
