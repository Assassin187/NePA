## Why

M0 needs a reproducible, reviewable baseline of gold inputs and deterministic validation before any planning or generated-code work can begin. This change turns the Chapter 5 artifact contracts and the Chapter 10.1 M0 work breakdown into the required schemas, validators, frozen default inputs, scope record, and sandbox image.

## What Changes

- Deliver M0-1 through M0-7 in the dependency order required by §10.1, including JSON Schemas with minimal examples, the MQTT-min scope record, gold Spec IR and Target Profile handling, deterministic lint commands, Test Bundle canonicalization, gold-input freeze records, and the sandbox image digest.
- Add the `nepa lint spec`, `nepa lint target`, and `nepa lint test-bundle` M0 CLI behavior, including the specified structured validation and failure codes. **BREAKING**: Target Profile validation accepts exactly the two §5.6.5 fields and rejects the historical client/server profile with `TARGET_ROLE_UNSUPPORTED`.
- Canonicalize only `gold_file/test_bundle.json`; preserve caller-supplied raw bytes for `gold_file/specIR.json` and `gold_file/target.json` and record their raw-byte SHA-256 values when freezing the default input combination.
- Keep M0's Test Bundle work declarative: no protocol test implementations, collection, execution, runner, oracle, workspace adapter, or external/public test contract is introduced.
- Apply the responsible-owner decision for the former scope blocker: crop Will and username/password CONNECT facts (`REQ-CONNECT-020` through `REQ-CONNECT-026`) from `gold_file/specIR.json` and remove their Test Bundle references so the gold inputs follow §7.1 without widening the design.

## Capabilities

### New Capabilities

- `gold-asset-validation`: Define deterministic M0 validation, canonicalization, scope-freeze, and input-freeze behavior for the MQTT gold Spec IR, Target Profile, and Test Bundle.

### Modified Capabilities

- None.

## Impact

- Affected implementation areas: `nepa/schemas/`, `nepa/speclib/lint.py`, the `nepa lint` CLI, `configs/scope-mqtt-min.yaml`, `gold_file/`, `docker/sandbox.Dockerfile`, and M0-only unit/schema validation assets.
- M0 dependencies: M0-3 depends on M0-1/M0-2; M0-3a depends on M0-1/M0-2/M0-3; M0-4 depends on M0-1; M0-5 depends on M0-1/M0-3/M0-3a; M0-6 depends on M0-3/M0-3a/M0-5; M0-7 depends on M0-3a.
- D0.5 (scope) and D0.6 (default-input freeze) require a responsible-person signature. Those are manual gates; an implementation agent may prepare records and verification output but cannot assert them complete.
- Out of scope: M1 `plan_lint` and Blueprint closure gates; all concrete protocol tests and M2 test assets; any runner, oracle, workspace adapter, marker collection, or public test contract; and any modification to `project_docs/system_design.md`.
