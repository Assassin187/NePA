import json
from pathlib import Path

from jsonschema import Draft202012Validator


SCHEMA_DIR = Path(__file__).parents[1] / "nepa" / "schemas"
EXAMPLE_DIR = SCHEMA_DIR / "examples"


def test_schema_examples():
    schema_paths = sorted(SCHEMA_DIR.glob("*.schema.json"))
    assert len(schema_paths) == 10

    for schema_path in schema_paths:
        example_name = schema_path.name.removesuffix(".schema.json") + ".example.json"
        example_path = EXAMPLE_DIR / example_name
        assert example_path.is_file(), f"missing example for {schema_path.name}"

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        example = json.loads(example_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(example)
