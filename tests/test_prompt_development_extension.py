import pytest

import nepa.calibration.s4_prompt_development as development


def test_current_development_surface_has_no_extension_or_single_slot_retry():
    assert not hasattr(development.PromptDevelopmentCoordinator, "expand")
    assert not hasattr(development.PromptDevelopmentCoordinator, "retry_extension_slot")
    assert not hasattr(development, "_combine_reports")

    parser = development._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["expand", "--development-root", "unused"])
    with pytest.raises(SystemExit):
        parser.parse_args(["retry-extension-slot", "--development-root", "unused"])
