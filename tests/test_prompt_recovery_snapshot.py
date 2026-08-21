import json
from pathlib import Path

from jsonschema import Draft202012Validator

from nepa.calibration.s4_prompt_development import RECOVERY_SEED_SHA256


def test_authorized_seed_hash_is_exact_and_snapshot_is_root_relative():
    seed = Path("experiments/m1-4a2-architecture-planner-prompt-optimization/results/phase1/artifacts/prompt-exact-algorithm.md")
    import hashlib
    assert hashlib.sha256(seed.read_bytes()).hexdigest() == RECOVERY_SEED_SHA256
    schema = json.loads(Path("nepa/schemas/calibration-recovery-prompt-snapshot.schema.json").read_text(encoding="utf-8"))
    value = json.loads(Path("nepa/schemas/examples/calibration-recovery-prompt-snapshot.example.json").read_text(encoding="utf-8"))
    value["prompt_ref"]["path"] = "/workspace/prompt.md"
    assert not Draft202012Validator(schema).is_valid(value)
