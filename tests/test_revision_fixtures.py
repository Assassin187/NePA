import json
from pathlib import Path

import pytest

from tools.generate_revision_mechanism_fixtures import OPERATORS, TRIGGER_CASES, generate


pytestmark = pytest.mark.revision_mechanism
ROOT = Path(__file__).resolve().parents[1]


def test_revision_fixture_generator_is_byte_stable_and_checked_in(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first)
    generate(second)
    for case_id in ("mqtt", "non_mqtt"):
        relative = Path(case_id) / "revision-mechanism-fixtures.json"
        assert (first / relative).read_bytes() == (second / relative).read_bytes()
        assert (first / relative).read_bytes() == (ROOT / "tests/fixtures/revision" / relative).read_bytes()


def test_revision_fixtures_close_trigger_and_operator_matrix():
    expected_triggers = {row[0] for row in TRIGGER_CASES}
    expected_operators = {row[0] for row in OPERATORS}
    for case_id in ("mqtt", "non_mqtt"):
        fixture = json.loads(
            (ROOT / "tests/fixtures/revision" / case_id / "revision-mechanism-fixtures.json").read_text(encoding="utf-8")
        )
        assert fixture["source_plan"]["schema_version"] == "5.0"
        assert {row["code"] for row in fixture["trigger_cases"]} == expected_triggers
        assert {row["operator"] for row in fixture["operator_cases"]} == expected_operators
        assert len(fixture["source_plan"]["task_uids"]) > 0


def test_revision_core_has_no_protocol_name_branch():
    source = (ROOT / "nepa/speclib/revision_mechanism.py").read_text(encoding="utf-8").lower()
    assert "mqtt" not in source
    assert "non_mqtt" not in source
