import json
from pathlib import Path

from jsonschema import Draft202012Validator


SCHEMA_ROOT = Path("nepa/schemas")


def test_recovery_schemas_are_closed_and_examples_validate():
    for schema_path in sorted(SCHEMA_ROOT.glob("calibration-recovery-*.schema.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        example = json.loads((SCHEMA_ROOT / "examples" / schema_path.name.replace(".schema.json", ".example.json")).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(example)
        invalid = dict(example)
        invalid["unknown"] = True
        assert not Draft202012Validator(schema).is_valid(invalid)


def test_ordinary_recovery_refs_reject_workspace_locator():
    schema = json.loads((SCHEMA_ROOT / "calibration-recovery-prompt-snapshot.schema.json").read_text(encoding="utf-8"))
    example = json.loads((SCHEMA_ROOT / "examples/calibration-recovery-prompt-snapshot.example.json").read_text(encoding="utf-8"))
    example["prompt_ref"] = {"workspace_path": "outside.json", "sha256": "a" * 64}
    assert not Draft202012Validator(schema).is_valid(example)
