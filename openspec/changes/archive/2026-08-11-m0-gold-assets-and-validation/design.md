## Context

See `proposal.md` for motivation. Chapter 10.1 prescribes the implementation sequence: schemas and validators precede gold validation; only the Test Bundle is canonicalized; all three post-validation input bytes are then frozen. The existing gold Spec IR is not presently scope-aligned with §7.1, so the planned M0 acceptance path includes an explicit owner-resolved blocker rather than an inferred design change.

## Goals / Non-Goals

**Goals:**

- Translate the enumerated Chapter 5 artifact contracts into M0 JSON Schemas and deterministic lint behavior.
- Establish the default MQTT server/C99 input set with the §7.1 scope record, a canonical Test Bundle, reproducible raw-byte hashes, and auditable human approvals.
- Provide the M0 Docker sandbox baseline and verifiable digest, without using it to execute protocol tests.

**Non-Goals:**

- No M1 `plan_lint`, Delivery Blueprint closure, runner, oracle, workspace adapter, concrete test generation, marker collection, or public test interface.
- No modification of `project_docs/system_design.md`, and no automatic owner signature or approval assertion.
- No canonical rewrite of the caller inputs `specIR.json` or `target.json`.

## Decisions

### Schema and lint boundary

M0 implements the Chapter 5 schemas named in M0-1 plus a minimal valid example per schema. Deterministic lints are limited to `spec`, `target`, and `test-bundle`: Spec IR checks §5.1.6 rules 1–3 and 5; gold coverage is added only when the manifest is explicitly supplied; Target Profile enforces its closed two-field form and C99 server support; Test Bundle validates only its declarative manifest data. Test Bundle lint also compares raw input bytes with the canonical JSON encoding, and `test-bundle --spec` performs the same MUST/MUST NOT coverage closure using only `task` and `s7_only` gates.

This separates machine-checkable structure and references from M2-only test collection. An alternative of validating nodeids by running pytest is rejected because §5.3 expressly says M0/M1 do not require test files or collection.

### Schema contract audit and negative states

The ten M0 Schemas are audited one by one against the Chapter 5 tables and explicit cross-field rules. The audit adds only constraints with direct design evidence, including Run v3 terminal conditions, UTC ISO8601 timestamps, and the documented segments coverage-ratio domain. Positive minimal examples remain required, and negative fixtures cover each corrected contract. An alternative of broad schema hardening based on inferred domain assumptions is rejected because it could change M0 behavior without a design decision.

### Canonical input and coverage closure

`lint_test_bundle` reads the source bytes, parses JSON, validates metadata, and compares the original bytes with the shared `canonical_json_bytes` output. It reports a structured validation error for a mismatch and never rewrites ordinary lint inputs. With `--spec`, the command invokes the same deterministic MUST/MUST NOT coverage helper used by gold Spec lint; no second validator or test collection path is introduced. An alternative of checking only semantic equivalence is rejected because §5 and §8.7 freeze the byte sequence itself.

### CLI architecture and exit codes

The repository exposes one Typer-based `nepa` command tree, as required by §8.1/§8.2; the existing argparse path is migrated rather than retained in parallel. Valid M0 lint returns `0`, controlled input/Schema/metadata/canonical/coverage failures return `20`, and only NePA internal errors return `1`, while the existing structured JSON report remains the user-visible diagnostic. An alternative of preserving argparse or returning `1` for all invalid inputs is rejected because it conflicts with §8.7 and makes controlled failures indistinguishable from NePA bugs.

### Byte and hash handling

The canonical serializer is exactly the Chapter 5 Python encoding: sorted keys, UTF-8, compact separators, no trailing newline, and no NaN/Infinity. M0 writes that encoding only to `gold_file/test_bundle.json`. It reads and hashes `specIR.json` and `target.json` as opaque caller bytes; validators parse them without rewriting them. Freeze records are generated only after successful Test Bundle canonicalization and record raw-byte SHA-256 for each final gold file.

An alternative of canonicalizing all three assets is rejected because §5.3.2 explicitly reserves canonicalization for the Test Bundle and §10.1 fixes the ordering.

### Scope, conflicts, and approvals

The scope configuration encodes §7.1's server-only MQTT-min inclusion/exclusion boundary. M0-3 and M0-5 perform the documented scope-alignment review before their acceptance commands. Observed conflicts are recorded as blockers with owner action; they never authorize a broadened scope or a design-document edit. M0-2's scope freeze and M0-6's default-input freeze use pending signature fields prepared by automation but can only be completed by the responsible person.

The alternative of silently pruning or accepting existing gold content is rejected: it would either alter a caller source outside the Test Bundle exception or make the implementation contradict the authoritative design.

### C99 rule and sandbox source of truth

The supported Target Profile is exactly `server` and C99. Its language/role and build-variant support comes from the built-in C99 rules, not from metadata added to Target Profile, templates, or the frozen run inputs. The sandbox Dockerfile supplies the §8.5 tool set, builds deterministically enough to capture an image digest, and its capability check stops at tool availability.

The alternative of creating a profile-selected backend, template hashes, or test-facing container interface is rejected because §5.6.5 and §8.5 do not authorize those M0 additions.

## Risks / Trade-offs

- [Current gold scope conflict] → Block M0-3/M0-5/D0.1–D0.3 until the designated owner reconciles the gold Spec IR and affected Test Bundle entries to §7.1; do not alter the design.
- [The prior 14/14 checklist overstated implementation completeness] → Add explicit pending contract-audit, negative-test, CLI, and final-regression tasks; do not treat the signed gold freeze as evidence that validator behavior is complete.
- [Manual gates remain unsigned] → Generate records and checks as pending, and expose D0.5/D0.6 as incomplete until an authorized signer supplies date and identity.
- [Future generated tests do not exist] → Validate only frozen manifest metadata now; defer collection, marker drift checks, runner/oracle/adapter implementation, and protocol execution to M2.
- [Docker digest can vary with base-image availability] → Record the produced immutable digest and the build environment evidence; do not treat a tag as the freeze identity.
- [CLI framework/exit-code migration can break scripts] → Add subprocess-level positive and negative tests and preserve the existing command names and structured JSON report.

## Migration Plan

1. Add schemas, examples, scope record, lints, Test Bundle canonicalization, freeze-record templates, and Dockerfile capability evidence in M0 dependency order.
2. Run only the M0 schema/lint/canonical/hash/image capability commands listed in `tasks.md`; do not invoke protocol-test collection or execution.
3. Stop at the recorded gold scope blocker if it remains unresolved; after an authorized input correction, rerun M0-3 through M0-6 in order and obtain the two human signatures.
4. Rollback consists of reverting unaccepted implementation artifacts; never restore a stale hash record over modified gold bytes or fabricate a signature.
5. Complete the contract-audit and negative-schema tasks, then correct Test Bundle raw-byte and `--spec` coverage behavior, migrate the single CLI path to Typer with the §8.7 exit codes, and run the final M0 regression.
6. Preserve the three signed gold inputs and their recorded hashes throughout this reconciliation; if their raw bytes remain unchanged, no re-signature is needed.
