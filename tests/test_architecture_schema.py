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


def test_layout_accepts_only_the_two_resolved_placeholder_pairs():
    schema = json.load(open("nepa/schemas/architecture-draft.schema.json", encoding="utf-8"))
    validator = Draft202012Validator(schema)

    message = copy.deepcopy(json.load(open("nepa/schemas/examples/architecture-draft.example.json", encoding="utf-8")))
    message["layout"]["files"][2].update(path=None, path_pattern="src/core/core_{message_id}.c", expand_over="messages")
    assert validator.is_valid(message)

    for pattern, domain in (
        ("src/core/core_{message_id}.c", "types"),
        ("src/core/core_{type_id}.c", "messages"),
        ("src/core/core_{message_id}_{message_id}.c", "messages"),
        ("src/core/core_{protocol_id}.c", "messages"),
        ("src/core/core_{message_id}_{type_id}.c", "messages"),
    ):
        invalid = copy.deepcopy(message)
        invalid["layout"]["files"][2].update(path=None, path_pattern=pattern, expand_over=domain)
        assert not validator.is_valid(invalid), (pattern, domain)
