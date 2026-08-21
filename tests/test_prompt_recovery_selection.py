import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_terminal_selection_and_no_selection_are_disjoint_closed_records():
    schema = json.loads(Path("nepa/schemas/calibration-recovery-terminal.schema.json").read_text(encoding="utf-8"))
    value = json.loads(Path("nepa/schemas/examples/calibration-recovery-terminal.example.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    assert validator.is_valid(value)
    invalid = copy.deepcopy(value)
    invalid["status"] = "fallback"
    assert not validator.is_valid(invalid)
