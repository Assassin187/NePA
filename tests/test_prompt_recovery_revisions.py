import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_revision_contract_allows_only_r1_or_r2_with_exact_predecessor():
    schema = json.loads(Path("nepa/schemas/calibration-recovery-revision.schema.json").read_text(encoding="utf-8"))
    value = json.loads(Path("nepa/schemas/examples/calibration-recovery-revision.example.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    assert validator.is_valid(value)
    invalid = copy.deepcopy(value)
    invalid["version"] = "r3"
    assert not validator.is_valid(invalid)
    invalid = copy.deepcopy(value)
    invalid["evidence_refs"] = []
    assert not validator.is_valid(invalid)
