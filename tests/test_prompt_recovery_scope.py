import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_recovery_handoff_admits_only_m1_4a3_and_no_downstream_claim():
    schema = json.loads(Path("nepa/schemas/calibration-recovery-handoff.schema.json").read_text(encoding="utf-8"))
    value = json.loads(Path("nepa/schemas/examples/calibration-recovery-handoff.example.json").read_text(encoding="utf-8"))
    assert Draft202012Validator(schema).is_valid(value)
    assert value["consumer"] == "m1-4a3"
    assert value["satisfies"]["m1_4a3_admission"] is True
    assert not any(result for name, result in value["satisfies"].items() if name != "m1_4a3_admission")
