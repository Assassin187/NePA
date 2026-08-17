import json

import pytest

from nepa.speclib.planning import PlanningInputError, prepare_architecture_inputs


def test_gold_inputs_preserve_spec_and_canonicalize_target():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    assert prepared.spec_bytes == open("gold_file/specIR.json", "rb").read()
    assert json.loads(prepared.target_bytes) == {"language": {"name": "C", "version": "C99"}, "roles": ["server"]}
    assert prepared.test_bundle_bytes == open("gold_file/test_bundle.json", "rb").read()


def test_noncanonical_test_bundle_is_rejected(tmp_path):
    bundle = json.loads(open("gold_file/test_bundle.json", encoding="utf-8").read())
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    with pytest.raises(PlanningInputError, match="canonical"):
        prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", path)


def test_preparation_does_not_require_test_nodeid_files():
    prepared = prepare_architecture_inputs("tests/fixtures/non_mqtt_application/spec.json", "tests/fixtures/non_mqtt_application/target.json", "tests/fixtures/non_mqtt_application/test_bundle.json")
    assert prepared.test_bundle["tests"][0]["nodeid"].startswith("tests/")
