"""Oracle tests use test doubles only; these are not live-generation evidence."""
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).parents[1]
behavior_spec = importlib.util.spec_from_file_location("mqtt_behavior", ROOT / "gold_file/mqtt/acceptance/mqtt_behavior.py")
behavior = importlib.util.module_from_spec(behavior_spec)
behavior_spec.loader.exec_module(behavior)
sys.modules["mqtt_behavior"] = behavior
spec = importlib.util.spec_from_file_location("sample_oracle", ROOT / "gold_file/mqtt/acceptance/mqtt_smoke.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)

class Socket:
    def __init__(self, data):
        self.data = data
    def recv(self, size):
        result, self.data = self.data[:size], self.data[size:]
        return result

def test_reference_wire_vector():
    assert oracle.connect_packet("x") == bytes.fromhex("100d00044d5154540402001e000178")
    assert oracle.connect_packet("x", 0)[8] == 0

def test_short_read_and_early_eof():
    assert oracle.receive(Socket(b"abcd"), 4) == b"abcd"
    with pytest.raises(AssertionError):
        oracle.receive(Socket(b"a"), 2)

def test_refusal_and_ping_expectations_are_not_process_survival():
    source = (ROOT / "gold_file/mqtt/acceptance/mqtt_smoke.py").read_text()
    assert 'data = sock.recv(1)' in source and 'require(data == b""' in source
    assert "subsequent-connection" in source
