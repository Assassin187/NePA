import json
from pathlib import Path

from jsonschema import Draft202012Validator

from nepa.config import load_config
from nepa.orchestrator import ControlledStageFailure, Orchestrator
from nepa.run_store import RunStore, SpecRunInputs


ROOT = Path(__file__).parents[1]


def test_controlled_exit_report_is_schema_valid_and_copies_reason(tmp_path):
    store = RunStore.initialize_spec_run(
        tmp_path,
        SpecRunInputs(ROOT / "gold_file/specIR.json", ROOT / "gold_file/target.json", ROOT / "gold_file/test_bundle.json"),
        load_config(overrides={"run": {"until": "s6"}}),
    )

    class Failing:
        def run(self, context):
            raise ControlledStageFailure({"code": "EXPECTED_FAILURE", "detail": "exact persisted reason"})

    assert Orchestrator({"s4": Failing()}).run_spec(store) == 20
    report = json.loads((store.root / "report/report.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "nepa/schemas/report.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)
    assert report["termination_reason"] == store.load_run()["termination_request"]["reason"]
    assert report["req_coverage"]["value"] is None
    assert report["req_coverage"]["status"] == "unavailable"
    assert (store.root / "report/report.md").is_file()
