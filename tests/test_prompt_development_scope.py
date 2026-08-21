from pathlib import Path


def test_prompt_development_does_not_register_a_public_nepa_command():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "prompt-development" not in pyproject
    assert not Path("nepa/cli_prompt_development.py").exists()
