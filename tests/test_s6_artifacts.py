import copy
import hashlib

import pytest
from jsonschema import Draft202012Validator

from nepa.schemas import load_example, load_schema
from nepa.speclib.lint import canonical_json_bytes
from nepa.speclib.plan_revision import PlanRevisionError, append_verification_committed, validate_revision_ledger


def test_attempt_schema_enforces_terminal_reference_conditions():
    schema = load_schema("s6-attempt.schema.json")
    attempt = load_example("s6-attempt.example.json")
    assert not list(Draft202012Validator(schema).iter_errors(attempt))
    broken = copy.deepcopy(attempt)
    broken["status"] = "failed"
    assert list(Draft202012Validator(schema).iter_errors(broken))


def test_verification_wal_requires_closed_complete_precommit_projection():
    schema = load_schema("verification-pending.schema.json")
    wal = load_example("verification-pending.example.json")
    assert not list(Draft202012Validator(schema).iter_errors(wal))
    for field in ("expected_commit", "expected_git_tree", "new_state", "changed_files"):
        broken = copy.deepcopy(wal)
        broken.pop(field)
        assert list(Draft202012Validator(schema).iter_errors(broken)), field
    nested_extra = copy.deepcopy(wal)
    nested_extra["old_state"]["unexpected"] = True
    assert list(Draft202012Validator(schema).iter_errors(nested_extra))


def test_revision_ledger_rejects_duplicate_verification_identity_even_when_conflicting():
    evidence = {
        "path": "test_results/task_evidence/0123456789abcdef/evidence_001.json",
        "sha256": "1" * 64,
    }
    ledger = append_verification_committed(
        {"schema_version": "2.0", "entries": []},
        task_uid="0123456789abcdef", evidence_ref=evidence, commit_sha="2" * 40,
    )
    duplicate = copy.deepcopy(ledger["entries"][0])
    duplicate["event_seq"] = 2
    duplicate["prev_entry_sha256"] = hashlib.sha256(canonical_json_bytes(ledger["entries"][0])).hexdigest()
    duplicate["payload"]["commit_sha"] = "3" * 40
    ledger["entries"].append(duplicate)
    with pytest.raises(PlanRevisionError, match="duplicate verification identity"):
        validate_revision_ledger(ledger)
