import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_quality_audit_is_typed_and_blind_review_unavailability_is_explicit():
    schema = json.loads(Path("nepa/schemas/calibration-recovery-quality-audit.schema.json").read_text(encoding="utf-8"))
    value = json.loads(Path("nepa/schemas/examples/calibration-recovery-quality-audit.example.json").read_text(encoding="utf-8"))
    assert Draft202012Validator(schema).is_valid(value)
    assert value["blind_review"]["status"] == "unavailable"
    invalid = copy.deepcopy(value)
    invalid["screening_score"] = 1
    assert not Draft202012Validator(schema).is_valid(invalid)
