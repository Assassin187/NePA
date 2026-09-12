import json
from pathlib import Path
import pytest
from nepa.speclib.lint import lint_target

ROOT = Path(__file__).parents[1]

def target():
    return json.loads((ROOT / "gold_file/target.json").read_bytes())

def test_target_lint_accepts_explicit_profile():
    assert lint_target(target(), ROOT / "gold_file/specIR.json")["valid"]

@pytest.mark.parametrize("mutation", [
    lambda t: t.update(roles=["client", "server"]),
    lambda t: t.update(language={"name": "C", "version": "C11"}),
    lambda t: t.update(backend="c99"),
    lambda t: t.pop("schema_version"),
    lambda t: t["builds"][0].update(artifact="../escape"),
    lambda t: t["builds"][1].update(artifact=t["builds"][0]["artifact"]),
    lambda t: t["builds"][0].update(required_flags=[]),
    lambda t: t["builds"][1].update(required_flags=["-std=c99", "-Wall", "-Wextra", "-Werror"]),
    lambda t: t.update(run=["sleep", "100"]),
])
def test_target_rejects_invalid_contract(mutation):
    value = target()
    mutation(value)
    assert not lint_target(value)["valid"]
