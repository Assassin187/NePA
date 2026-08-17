import copy
import json

from jsonschema import Draft202012Validator


def test_architecture_schema_is_closed_and_2020_12():
    schema = json.load(open("nepa/schemas/architecture-draft.schema.json", encoding="utf-8"))
    example = json.load(open("nepa/schemas/examples/architecture-draft.example.json", encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example)
    forbidden = copy.deepcopy(example)
    forbidden["task_id"] = "T-001"
    assert not Draft202012Validator(schema).is_valid(forbidden)
