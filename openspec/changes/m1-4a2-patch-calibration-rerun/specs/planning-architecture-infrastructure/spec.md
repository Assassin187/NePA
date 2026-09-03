## MODIFIED Requirements

### Requirement: Lineage identity freezes every non-prompt comparison variable
The system SHALL derive a `lineage_id` from the existing canonical lineage manifest that binds the frozen input references, planning-index and Delivery-Constraints construction, ArchitectureDraft Schema, semantic-repair patch Schema, canonical serializer, atomic patch application semantics, failure-to-allowed-path locality mapping, deterministic coupled-reference projection, path-neutrality token derivation, `ARCH_VALIDATE`, exactly the configured Claude/Qwen/DeepSeek candidate model request configurations, calibration statistical definitions and the two-stage ArchitecturePlanner invocation contract. Each prompt version SHALL record one bundle version, the existing artifact references for `architecture_planner_initial.md` and `architecture_planner_repair.md`, and both source byte sequences outside `lineage_id`; at each admitted version transition exactly one stage may change while the other remains byte-identical. Any bound non-prompt component change SHALL produce a different lineage id; evidence from different lineages or from the former single-template invocation contract SHALL NOT be aggregated or compared as one prompt-bundle experiment. The implementation SHALL NOT add a bundle digest, dedicated template-hash fields or a new hash precondition for this identity. (Design: §0.1, §6.4.8.1～§6.4.8.3, §8.3, §9.2.)

#### Scenario: One stage prompt changes
- **WHEN** a later bundle version changes exactly one stage's bytes while the other stage and every lineage-bound component remain identical
- **THEN** the version remains in the same lineage and records the parent bundle version, both existing prompt artifact references and the single-stage diff without adding a prompt or bundle hash gate

#### Scenario: Both stage prompts change together
- **WHEN** a proposed version changes both `initial` and `repair` bytes or combines stages from different bundle versions
- **THEN** the version is rejected before Provider I/O and no trial is declared

#### Scenario: Patch behavior changes
- **WHEN** the patch Schema, application semantics or failure-to-path locality mapping changes
- **THEN** the manifest produces a different lineage id and prior full-draft or patch evidence cannot enter the new comparison

#### Scenario: Validator token derivation or coupled projection changes
- **WHEN** path-neutrality token derivation or deterministic layout-reference projection changes
- **THEN** the manifest produces a different lineage id and all evidence under the prior behavior remains audit-only

### Requirement: Semantic repair is explicit, fresh, and bounded by the declared protocol
The M1-4a trial engine SHALL render only the selected bundle's `initial` template for depth zero and validate the first Schema-legal ArchitectureDraft immediately with `ARCH_VALIDATE`. Under the M1-4a2 protocol, each permitted semantic repair SHALL be a new no-history ArchitecturePlanner invocation that renders only the same bundle version's `repair` template with unchanged frozen inputs, the current Schema-valid candidate, the exact canonical failure list and the mechanically derived model-editable target paths. The repair SHALL return only a Schema-valid patch operation list with operation-specific presence state and no prior-value hash or value digest. The controller SHALL reject the complete patch if any operation is conflicting, violates its presence rule, cannot apply or is outside the model-editable paths. When an allowed operation changes a layout `path` or `path_pattern`, the controller SHALL derive a unique old-to-new expanded-path mapping and SHALL mechanically substitute only exact matching references in `modules[].owns_files` and `work_packages[].allowed_files`; these derived substitutions SHALL be recorded separately and SHALL NOT grant the model authority over those collections. The controller SHALL reject ambiguous or incomplete coupled closure. Otherwise it SHALL commit the model operations and deterministic projections as one atomic transition, preserve every other canonical value, revalidate the resulting complete ArchitectureDraft against its Schema and rerun the full validator. No rejected patch SHALL trigger full-draft replacement. Schema-format repair SHALL remain inside the M1-2 logical completion, repair only the declared output payload shape and be counted separately. The infrastructure SHALL support recording zero, one or two semantic repairs but SHALL NOT choose prompt-bundle versions, qualification dispositions or production repair budgets. It SHALL reuse the existing per-call trace prompt record and SHALL NOT add bundle/template digest fields or hash gates. (Design: §0.1, §6.4.8.1～§6.4.8.3, §8.4, §8.8.)

#### Scenario: Initial candidate passes
- **WHEN** the first Schema-legal ArchitectureDraft passes every architecture gate
- **THEN** the trial records p0 success after rendering only the `initial` template and issues no semantic patch call

#### Scenario: First patch is valid and local
- **WHEN** every patch operation satisfies its operation-specific presence rule and targets a path allowed by the current canonical failures
- **THEN** all operations apply atomically, untouched canonical values remain equal, and the complete patched draft is Schema-checked and fully revalidated as p1

#### Scenario: Second repair is allowed
- **WHEN** the p1 candidate remains Schema-valid but fails `ARCH_VALIDATE` and the declared protocol permits depth two
- **THEN** the second call renders only the same bundle's `repair` template with the p1 candidate, its newly recomputed failures and allowed paths and is evaluated as p2

#### Scenario: Patch is invalid or non-local
- **WHEN** one operation violates its presence rule, conflicts, cannot apply or targets a path outside the allowed set
- **THEN** no operation is committed, the prior candidate remains unchanged, the rejection is recorded and no full-draft fallback occurs

#### Scenario: Layout path has exact coupled references
- **WHEN** an allowed layout path operation has a unique old-to-new expansion and exact old-path references in module ownership or work-package allowed files
- **THEN** the controller substitutes only those matching references in the same atomic transition and records the model and derived changes separately

#### Scenario: Layout path closure is ambiguous
- **WHEN** a layout path operation changes its expansion domain, has no unique old-to-new correspondence or requires a change outside the two declared reference collections
- **THEN** the whole patch is rejected without broadening the allowed paths or committing a partial projection

### Requirement: Path-neutrality token derivation preserves legal placeholders and derived identifiers
`arch_15` SHALL recognize `{message_id}` and `{type_id}` as atomic legal placeholder tokens and SHALL recognize exact identifiers from the frozen Spec-derived identifier set, including identifiers containing separators, before tokenizing the remaining literal path and purpose text. A derived identifier SHALL authorize only its exact deterministic span and SHALL NOT authorize adjacent or partially matching undeclared text. Remaining tokens SHALL still be checked against the unchanged generic responsibility whitelist union the frozen derived-identifier set. The correction SHALL NOT add generic whitelist entries, disable path-neutrality, change the ArchitectureDraft Schema or make protocol-specific vocabulary universally valid. (Design: `pipeline_design_s4_s9.md` §5.2.2 and §5.2.4; `system_design.md` revision 5.2.0/5.3.0 and §6.4.8.1.)

#### Scenario: Legal placeholders are validated
- **WHEN** a Schema-valid layout uses exactly `{message_id}` with `expand_over=messages` or `{type_id}` with `expand_over=types`
- **THEN** `arch_15` evaluates the placeholder as one legal token and does not emit `ARCH_PATH_TOKEN_INVALID` for `message`, `type` or `id` fragments

#### Scenario: Exact derived identifier contains separators
- **WHEN** a path or purpose contains an exact frozen Spec-derived identifier with separators
- **THEN** `arch_15` evaluates that identifier atomically while continuing to validate all surrounding literal tokens

#### Scenario: Undeclared vocabulary remains invalid
- **WHEN** a path or purpose contains protocol-specific or other literal text that is neither an exact frozen derived identifier nor in the generic responsibility whitelist
- **THEN** `arch_15` still emits `ARCH_PATH_TOKEN_INVALID` and no placeholder rule masks the invalid text

### Requirement: Trial artifacts and calibration reports are hash-bound and recomputable
Calibration evidence SHALL be stored under `runs/_calibration/s4-architecture/<lineage_id>/<prompt_version>/<model_slot>/`. Each model root SHALL contain canonical batch, trial request/response and validation indexes, a dedicated trace/cache area, and a calibration report. Request/response indexes SHALL continue using the existing artifact-reference contract for every initial response, format repair, semantic patch payload, patch application/locality result, resulting candidate and validation item. Recomputing a report from the lineage, batch, trial, patch, validation and trace leaves SHALL reproduce canonical report bytes and SHALL reject missing, mutated, duplicate, cross-lineage, cross-bundle or cross-model evidence. The two-stage split SHALL NOT add any bundle hash, second template digest field or new defensive hash check. (Design: §0.1, §5.5, §6.4.8.1～§6.4.8.2, §9.1.5.)

#### Scenario: Complete model batch is recomputed
- **WHEN** all declared initial and patch records and their referenced evidence are present and valid
- **THEN** report recomputation produces a byte-identical calibration report including p0/p1/p2 and patch-locality results

#### Scenario: Patch evidence changes
- **WHEN** a patch payload, application result, allowed-path proof, resulting candidate or validation no longer matches its recorded SHA-256
- **THEN** recomputation fails and publishes no replacement report over the existing evidence

#### Scenario: Batch is partial
- **WHEN** fewer than the declared initial-generation trials are durably complete
- **THEN** batch status remains incomplete and no headline rate is represented as a complete result

### Requirement: Calibration metrics preserve fixed denominators and failure evidence
Each complete per-model report SHALL use the declared initial-trial count N as the denominator for Schema rates, first-pass rates, p0, p1 and p2 when the corresponding repair depth was declared. p0 SHALL count first Schema-legal candidates passing without semantic repair; p1 and p2 SHALL be cumulative success within at most one or two semantic repairs. A Schema failure, absent semantic candidate, rejected patch or exhausted repair depth SHALL remain failure at the applicable depths in the fixed denominator. Reports SHALL include unconditional k/N results for `arch_01`～`arch_15` at p0, p1 and p2; gate-failure co-occurrence; p0→p1 and p1→p2 gain/regression; patch locality and rejection reasons; and per-trial and aggregate initial, format-repair and semantic-patch calls, tokens, cost, latency, finish reasons, truncation, model identity and parameter support. Undeclared depths SHALL be null with a machine-readable reason rather than zero. (Design: §6.4.8.2～§6.4.8.3, §9.1.5, §9.2.)

#### Scenario: Trial first passes after the second patch
- **WHEN** the initial and p1 candidates fail but the p2 candidate passes all fifteen gates
- **THEN** the trial is excluded from p0 and p1, included in p2, and contributes exact per-depth gate transitions and usage

#### Scenario: Patch is rejected
- **WHEN** a declared semantic patch is rejected before application
- **THEN** the trial remains in N, the applicable cumulative rate remains failed, and the rejection and usage remain visible

#### Scenario: Only one repair depth is declared
- **WHEN** another calibration protocol declares a maximum semantic depth of one
- **THEN** p2 and p1→p2 diagnostics are null with an undeclared-depth reason and are not reported as zero
