import json
from pathlib import Path
import pytest
from nepa.config import load_config
from nepa.report import publish_report
from nepa.run_store import RunStore
from nepa.speclib.lint import _schema_errors

ROOT = Path(__file__).parents[1]
SCHEMAS = ROOT / "nepa/schemas"

@pytest.mark.parametrize("name", ["specs-requirements", "target-profile", "acceptance", "agent-action", "plan", "run", "report"])
def test_packaged_example_matches_schema(name):
    example = json.loads((SCHEMAS / "examples" / (name + ".example.json")).read_bytes())
    assert not _schema_errors(example, name + ".schema.json")

def test_actual_plan_run_and_failed_report_match_current_contracts(tmp_path):
    store = RunStore.initialize(tmp_path, ROOT / "gold_file/mqtt/specIR.json", ROOT / "gold_file/mqtt/target.json",
                                ROOT / "gold_file/mqtt/acceptance.json", load_config())
    assert not _schema_errors(store.plan(), "plan.schema.json")
    assert not _schema_errors(store.run, "run.schema.json")
    store.run.update(status="failed", exit_code=2, reason="test-only interrupted attempt")
    report = publish_report(store)
    assert not _schema_errors(report, "report.schema.json")
