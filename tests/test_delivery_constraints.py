import copy

import pytest

from nepa.speclib.delivery import DeliveryConstraintError, compile_delivery_constraints
from nepa.speclib.planning import prepare_architecture_inputs


def test_gold_delivery_constraints_have_stable_expanded_slots():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    first = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    second = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    assert first == second
    assert first["naming"]["symbol_prefix"] == "mqtt"
    assert any(slot["rule_id"] == "message-codecs" for slot in first["file_slots"])


def test_unsupported_target_is_rejected_before_compilation():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    target = copy.deepcopy(prepared.target_profile)
    target["language"]["version"] = "C11"
    with pytest.raises(DeliveryConstraintError) as exc:
        compile_delivery_constraints(prepared.spec, target)
    assert exc.value.code == "TARGET_LANGUAGE_UNSUPPORTED"


def test_message_and_type_derived_c_names_share_one_namespace():
    prepared = prepare_architecture_inputs("gold_file/specIR.json", "gold_file/target.json", "gold_file/test_bundle.json")
    spec = copy.deepcopy(prepared.spec)
    spec["messages"][0]["id"] = "shared-name"
    spec["types"][0]["id"] = "shared-name"
    with pytest.raises(DeliveryConstraintError) as exc:
        compile_delivery_constraints(spec, prepared.target_profile)
    assert exc.value.code == "DERIVED_IDENTIFIER_COLLISION"


def test_generic_server_abi_is_protocol_neutral_and_explicit():
    prepared = prepare_architecture_inputs("tests/fixtures/non_mqtt_application/spec.json", "tests/fixtures/non_mqtt_application/target.json", "tests/fixtures/non_mqtt_application/test_bundle.json")
    constraints = compile_delivery_constraints(prepared.spec, prepared.target_profile)
    abi = constraints["server_abi"]
    assert abi["events"] == ["connect", "bytes", "disconnect", "tick"]
    assert abi["net_layer_owns_io_only"] is True
    assert "mqtt" not in str(abi).lower()
