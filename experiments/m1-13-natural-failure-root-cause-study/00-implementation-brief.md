# M1-13 implementation brief

## 1. Input artifacts and Schema references

- `gold_file/specIR.json` — `nepa/schemas/specs-requirements.schema.json`.
- `gold_file/target.json` — `nepa/schemas/target-profile.schema.json`.
- `gold_file/test_bundle.json` — `nepa/schemas/test-bundle.schema.json`.
- `study-config.yaml`, resolved through `nepa.config.ResolvedConfig`; frozen into Run v4 `run.json` under `nepa/schemas/run.schema.json`.
- Each admitted Run's active/initial Plan, Plan State/history, revision ledger, S6 receipt, attempts, diagnostics, build/smoke artifacts, trace, and workspace Git facts, using their existing repository Schemas.

## 2. Output artifacts and acceptance commands

- `01-preregistration.md`, `02-sample-manifest.json`, `03-root-cause-results.json`, `04-root-cause-report.md`, `05-parameter-recommendation.json`, `06-owner-decision.md`, and `acceptance-record.md`.
- Raw evidence remains under ignored `runs/`; tracked records contain relative SHA-256 references.
- Acceptance is limited to the dedicated M1-13 pytest module, audit `--check`, Ruff/Mypy on the M1-13 Python paths, strict validation of this change, focused provider identity tests, and `git diff --check`. Repository-wide pytest and all-change OpenSpec validation are excluded by user direction.

## 3. Intended function signatures

- `parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace`
- `load_protocol(study_root: Path) -> dict[str, Any]`
- `validate_protocol(protocol: Mapping[str, Any], study_root: Path, runs_root: Path) -> None`
- `validate_manifest(manifest: Mapping[str, Any], protocol: Mapping[str, Any], study_root: Path, runs_root: Path) -> list[dict[str, Any]]`
- `validate_recommendations(record: Mapping[str, Any], results: Mapping[str, Any]) -> None`
- `compute_results(protocol: Mapping[str, Any], manifest: Mapping[str, Any], run_packages: Sequence[Mapping[str, Any]]) -> dict[str, Any]`
- `render_report(results: Mapping[str, Any], manifest: Mapping[str, Any], recommendations: Mapping[str, Any]) -> str`
- `run(study_root: Path, runs_root: Path, *, check: bool, protocol_check: bool) -> None`
- `main(argv: Sequence[str] | None = None) -> int`

## 4. Governing design sections

- `project_docs/system_design.md`: §4.7, §4.8, §5.2.4, §5.4, §5.5, §6.6, §6.6.1, §8.3, §8.4, §9.1.4, §9.2, §10.2.1, §10.2.2 (M1-13), §10.2.3, §10.8, PQ-1, and D1.14.
- `project_docs/pipeline_design_s4_s9.md`: §5.6, §6.1, §9, §10, §11 PQ-1, and §13.4.
- Current change: proposal, delta specification, all design decisions, migration plan, and tasks. The responsible owner's 2026-09-11 authorization additionally permits only the minimal returned-model observation correction described in the change artifacts.
