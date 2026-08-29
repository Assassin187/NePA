import json
from pathlib import Path

from jsonschema import Draft202012Validator

from nepa.calibration.s4_prompt_development import scan_prompt_neutrality


def test_recovery_prompt_seed_has_no_historical_fixed_hash_contract():
    seed = Path("nepa/agents/prompts/architecture_planner.md")
    scan_prompt_neutrality(seed.read_bytes())
    schema = json.loads(Path("nepa/schemas/calibration-recovery-prompt-snapshot.schema.json").read_text(encoding="utf-8"))
    value = json.loads(Path("nepa/schemas/examples/calibration-recovery-prompt-snapshot.example.json").read_text(encoding="utf-8"))
    value["prompt_ref"]["path"] = "/workspace/prompt.md"
    assert not Draft202012Validator(schema).is_valid(value)
