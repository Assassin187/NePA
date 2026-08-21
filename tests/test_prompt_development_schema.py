import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_prompt_development_schema_examples_are_closed_and_valid():
    schema_dir = Path("nepa/schemas")
    example_dir = schema_dir / "examples"
    names = (
        "calibration-development-protocol",
        "calibration-prompt-version",
        "calibration-prompt-snapshot",
        "calibration-prompt-revision",
        "calibration-attempt-declaration",
        "calibration-attempt-outcome",
        "calibration-development-extension",
        "calibration-development-assessment",
        "calibration-development-outcome",
        "calibration-development-selection",
    )
    for name in names:
        schema = json.loads((schema_dir / f"{name}.schema.json").read_text(encoding="utf-8"))
        example = json.loads((example_dir / f"{name}.example.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(example)
        encoded = json.dumps(example, sort_keys=True)
        assert "sentinel" not in encoded
