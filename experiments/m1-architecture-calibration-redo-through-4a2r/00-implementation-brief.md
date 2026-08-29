# M1-4a2r implementation brief

Status: entry-gate inventory recorded before production implementation.
Date: 2026-08-26
Change: `m1-architecture-calibration-redo-through-4a2r`

This brief is a derived implementation index. `project_docs/system_design.md` remains the controlling design source; this file adds no design decision.

## 1. Input artifacts and Schema references

### Frozen M0 and archived dependencies

- M0 freeze: `configs/m0-default-inputs.freeze.yaml`, status `confirmed`, date `2026-08-11`, confirmer `ljf`.
- M0 raw-byte SHA-256 checks:
  - `gold_file/specIR.json`: `a0ec9616eb06c206416a93220e1ea630d04166eb17e102bc9d9476fe2694aa09`
  - `gold_file/target.json`: `efa8dc8fc0914d5b563a1da1aeaad1a7a277b4b161b89893b8efa75d6818b49b`
  - `gold_file/test_bundle.json`: `8f77eb4c5a15ef0ee02979240fbcd4eebdf585d5ad023d130de9468e791d343c`
- Archived M1 dependencies are complete by their recorded task files: M1-1 `18/18`, M1-2 `19/19`, and M1-3 `15/15`.
- The M0 entry condition is therefore evidenced by the confirmed three-input freeze and the archived dependency records; M1-4a1-specific free-layout lineage conditions remain to be established by this change.

### Current implementation inventory

The following existing production path is the migration target and is not treated as new-lineage evidence until updated and revalidated:

- `nepa/speclib/delivery.py`: `compile_delivery_constraints` currently constructs the fixed `file_rules`/file-slot projection and only expands message slots.
- `nepa/speclib/planning.py`: `prepare_architecture_inputs`, `build_planning_index`, and context preflight consume the existing planning and fixed-constraint path.
- `nepa/speclib/architecture.py`: `load_architecture_draft`, canonical serialization, and `validate_architecture` currently expose the ten-gate ArchitectureDraft/ARCH_VALIDATE path.
- `nepa/calibration/s4_architecture.py`: `build_lineage_manifest`, isolated trial execution, repair locality, reports, and recomputation currently bind the predecessor calibration contract.
- `nepa/calibration/s4_prompt_development.py`: development/recovery coordination currently contains the historical two-slot and old screening/recovery contract.
- `nepa/agents/base.py` and `nepa/agents/prompts/architecture_planner.md`: the existing renderer and five-section ArchitecturePlanner template are the shared prompt path to be migrated.

### Historical-only evidence inventory

The following bytes are retained as provenance and must not enter the new denominator, fallback tuple, selection, or handoff. Directory hashes are deterministic manifests of relative path, file length, and file SHA-256; they are inventory anchors, not new lineage ids.

- Historical experiment directory `experiments/m1-4a2-architecture-planner-prompt-optimization/`: 463 files, manifest SHA-256 `2e4b6389ea5821cfbe5a79d3a2e2fd8d8ff7b8b684f4afc361bd910a105c0f8b`.
- Historical calibration roots under `runs/_calibration/s4-architecture/`:
  - `49cce2766275f525761fd2a4035df00b20754538f37f9c62bdba061f3bfdbd2a`: 149 files, `886f09e98feae52cee3718298013be4bcc263f697a767b585e505b723910096e`
  - `586aef1c0fd52eee0f10e828bf7552a1d2d45a18f6ea119877e24f053a5a7bb7`: 327 files, `90353b546474a87bdd76f2a76b3a160d258b9ba26e1adfa27660f5a18aa6e290`
  - `8e0336e9ba795fb913604961a9559aae3433f5e44d81e1571089a1f4785b6d13`: 185 files, `9508ad987fde5231d3aab80c0df480c3d70618bbd69c642a5a70277c40c88c5e`
  - `b651aa5c2118add34551a8814859e352c01268548a86b4d704add63b33011ebf`: 491 files, `e16625ff45d025b93d9d645bb907094422e4c47d5168a220c489e6263dfc548e`
  - `daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772`: 458 files, `0f848ea5322e6e74b73d5327ed8251568fec903d0c9ea98b7ecfd3d76ccc4c82`

### Resolved design baseline freeze

Tasks 1.2 and 1.4 have been applied before this freeze. The approved bytes currently used by this change are:

- `project_docs/system_design.md` (design version 5.3.0): `49ec7593600b09a1d6c7ea7d748b9e8843eaa77c7e86e6cdb4784f720227d8e8`.
- `project_docs/pipeline_design_s4_s9.md` (authorized subdocument version 1.1.0): `6ebf3c693e519fd14229b3591b4226d51c9df399380f28164d5d981d45c51af8`.

The synchronized baseline was checked for the complete placeholder pairs `{message_id}`/`messages` and `{type_id}`/`types`, their `per_message`/`per_type` projections, and the resolved `arch_13`/`arch_15` criteria (`link_source`, exact `entry_point`, unique output paths, acyclic graph, and validator-side whitelist over path/purpose tokens). `kind`/`producer` derivation remains deferred to M1-4b2 and is not invented here.

## 2. Output artifacts and acceptance commands

The implementation will produce only the scoped outputs below and their required tests/evidence:

- Versioned protocol-neutral layout convention under `nepa/assets/layout_conventions/`.
- Free-layout Delivery Constraints, ArchitectureDraft and fifteen-gate ARCH_VALIDATE contracts, examples, canonical evidence and deterministic tests.
- Three-slot calibration configuration, lineage/artifact/recompute path, development/recovery protocol artifacts, and protocol-neutral MQTT/non-MQTT fixtures.
- New tracked experiment records under this directory: this brief, preregistration, non-secret provenance, machine summaries, reports, and conditional recovery status.
- New raw trial evidence, if live work is authorized and executed, under `runs/_calibration/s4-architecture/<new-lineage>/`; historical roots above remain untouched.

Required acceptance commands are recorded here before implementation and are expanded by `tasks.md`:

```text
uv run python -m pytest -q tests/test_spec_lint.py tests/test_target_lint.py tests/test_test_bundle_lint.py tests/test_run_store.py tests/test_llm_providers.py tests/test_agent_invoker.py
uv run python -m pytest -q tests/test_delivery_constraints.py tests/test_planning_inputs.py tests/test_planning_protocol_neutrality.py tests/test_architecture_schema.py tests/test_architecture_validation.py tests/test_architecture_calibration.py tests/test_architecture_repair_locality.py tests/test_calibration_artifacts.py tests/test_calibration_lineage.py tests/test_calibration_reporting.py tests/test_prompt_development*.py tests/test_prompt_recovery*.py tests/test_schema_examples.py
uv run python -m pytest -q
openspec validate --all --strict
git diff --check
```

Direct `python -m pytest` was also attempted for the entry set but this environment's system Python has no pytest module; the equivalent project-managed command passed 67 tests.

## 3. Function and class signature index

The existing public/internal entry points to preserve or migrate are:

- `nepa.speclib.delivery.compile_delivery_constraints(spec, target_profile) -> dict[str, Any]`
- `nepa.speclib.planning.prepare_architecture_inputs(spec, target, test_bundle, *, config=None) -> PreparedArchitectureInputs`
- `nepa.speclib.planning.build_planning_index(spec, target, test_bundle, constraints) -> dict[str, Any]`
- `nepa.speclib.planning.preflight_architecture_planner_context(...) -> ...`
- `nepa.speclib.architecture.load_architecture_draft(source) -> dict[str, Any]`
- `nepa.speclib.architecture.serialize_architecture_draft(draft) -> bytes`
- `nepa.speclib.architecture.validate_architecture(draft, planning, manifest, constraints) -> dict[str, Any]`
- `nepa.speclib.architecture.validate_architecture_result(result) -> None`
- `nepa.calibration.s4_architecture.build_lineage_manifest(...) -> ...`
- `nepa.calibration.s4_architecture.ArchitectureCalibrationDriver`
- `nepa.calibration.s4_architecture.recompute_calibration_report(model_root, *, config=None) -> dict[str, Any]`
- `nepa.calibration.s4_prompt_development.preflight_calibration_config(...) -> CalibrationPreflight`
- `nepa.calibration.s4_prompt_development.PromptDevelopmentCoordinator`
- `nepa.calibration.s4_prompt_development.PromptRecoveryCoordinator`
- `nepa.calibration.s4_prompt_development.main(argv=None) -> int`
- `nepa.agents.base.PromptRenderer`, `nepa.agents.base.render_prompt(...)`, and `nepa.agents.base.AgentInvoker`

The implementation may add only the minimum layout-convention loader/canonicalizer and validator-side shared whitelist machinery required by the design; it must not create a parallel production validator or a second calibration path.

## 4. Referenced design chapters

- Main design: `system_design.md` §§0.1, 4.2, 4.6-4.8, 5.2, 5.5, 5.6.5.2-5.6.5.5, 6.4.1-6.4.4, 6.4.8-6.4.8.3, 8.3-8.4, 8.8, 9.2, 10.2, 10.8, 11.3, 12.5.
- Authorized S4-S9 subdocument: `pipeline_design_s4_s9.md` §§0.1, 5.1-5.2.5, 5.4-5.5, 6.4-6.5, 8-9, 14.
- This change's controlling OpenSpec artifacts: `proposal.md`, `design.md`, `specs/planning-architecture-infrastructure/spec.md`, `specs/architecture-prompt-development/spec.md`, `specs/architecture-calibration-recovery/spec.md`, and `tasks.md`.

## Supersession inventory

The active `m1-4a2-architecture-planner-prompt-optimization` change is the superseded predecessor for this implementation. Before production implementation begins, it must be archived with:

```text
openspec archive m1-4a2-architecture-planner-prompt-optimization --skip-specs -y
```

Its recorded state is `34/36`; its `tasks.md` SHA-256 is `546eb2368bc2586ad22391a1978cf0973048a90ce89ed54430c936ef229dd4e8`, and the pre-archive file is 29,548 bytes. The archive must preserve those bytes/status as historical provenance while preventing its incompatible dual-model capability delta from entering the baseline. No completion claim is made for that change. The command completed as `openspec/changes/archive/2026-08-26-m1-4a2-architecture-planner-prompt-optimization/`. Its archived `.openspec.yaml`, `proposal.md`, `design.md`, and dual-model spec files remain under that path; the archived `tasks.md` retains the same SHA-256 and 34/36 state. `openspec list --json` now reports only this change as active, `openspec validate --all --strict` passes, and `openspec/specs/` has no `architecture-prompt-development` directory.

## Entry constraints

No production Schema, validator, prompt, lineage, or Provider work may start until the authorized design baseline is frozen after tasks 1.2 and 1.4, the superseded change is archived, and task 1.3 records the resulting main/subdocument paths and SHA-256 values. No live credential values are read or stored by this brief.
