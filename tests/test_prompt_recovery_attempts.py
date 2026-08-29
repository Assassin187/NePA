import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def _schema():
    return json.loads(Path("nepa/schemas/calibration-recovery-attempt-declaration.schema.json").read_text(encoding="utf-8"))


def _example():
    return json.loads(Path("nepa/schemas/examples/calibration-recovery-attempt-declaration.example.json").read_text(encoding="utf-8"))


def test_attempt_declares_one_coherent_three_model_n5_batch():
    validator = Draft202012Validator(_schema())
    assert validator.is_valid(_example())
    invalid = copy.deepcopy(_example())
    invalid["trial_ids"]["qwen"].pop()
    assert not validator.is_valid(invalid)


def test_attempt_rejects_replacement_or_unknown_model_slots():
    validator = Draft202012Validator(_schema())
    invalid = copy.deepcopy(_example())
    invalid["trial_ids"]["qwen"].append("trial_006")
    assert not validator.is_valid(invalid)
    invalid = copy.deepcopy(_example())
    invalid["model_ids"] = ["qwen"]
    assert not validator.is_valid(invalid)
