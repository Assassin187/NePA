## Context

See `proposal.md` for motivation. The current implementation already has the three logical model slots, free-layout ArchitectureDraft, fifteen-gate validator, p0/p1/p2-capable trial reporting, an M1-4a2 coordinator, and a recovery locality policy. Its development path is nevertheless hard-coded to V0/V1/V2, N=5 plus optional N=10 extensions, semantic depth one, p1=0.80, and repair responses that replace the complete draft. The recovery coordinator shares parts of that infrastructure and must retain its existing R0～R2/full-draft semantics.

The authoritative baseline is `project_docs/system_design.md` 6.1.0. A new live run cannot reuse the prior lineage because patch Schema/application/locality, semantic depth, version budget, metric decision rule and the single-template-to-two-stage invocation change are lineage-controlled. Raw historical evidence and the archived change are provenance only. The 6.0.0 lineage that required model-authored prior-value hashes and all later single-template lineages are historical and cannot enter the new denominator.

The first 6.0.1 value-hash-free execution produced one infrastructure-invalid lineage and one complete V0/V1 lineage before stopping. Its evidence shows that the Schema/design-legal `{type_id}` form is split into `type` and `id` by `arch_15`, and that changing a layout path without its exact path-reference projection can regress `arch_02`, `arch_09` and `arch_12`. These are validator/locality defects rather than prompt evidence. The owner has authorized correcting them and restarting with a new post-fix lineage whose 15 initial-generation trials per model are counted from zero. Pre-fix calls remain visible as audit usage but are outside the post-fix development budget and comparison set.

## Goals / Non-Goals

**Goals:**

- Extend the existing calibration path so a declared M1-4a2 protocol can use a complete initial ArchitectureDraft followed by at most two patch-only semantic repairs.
- Make patch admission, application, locality, p0/p1/p2 metrics, V0～V4 progression and selection fully deterministic and recomputable.
- Make `arch_15` recognize legal placeholders and exact Spec-derived identifiers without admitting unrelated literal tokens, and close layout-path repairs over only their mechanically coupled file-reference projections.
- Preserve one ArchitecturePlanner role and model route while replacing its single shared prompt with one shared, protocol-neutral `initial`/`repair` prompt bundle used identically by all three slots.
- Make every V0～V4 version an inseparable prompt pair and permit exactly one stage-template edit per admitted revision, with four total revision opportunities across the bundle.
- Reuse existing trace and artifact-reference mechanisms without adding bundle hashes, dedicated template digest fields or new defensive hash gates.
- Preregister and execute one fresh live development lineage after deterministic gates pass, ending in selection/handoff or a tie.

**Non-Goals:**

- Changing M1-4a2r's full-draft repair output semantics or thresholds, M1-4a3 qualification thresholds, production S4 repair budgets, B1～B4, the validator's architecture acceptance criteria, the path-neutrality whitelist, or the free-layout contract. M1-4a2r and formal calibration adopt the same phase-based prompt selection only because the former single runtime template is removed; their output contracts and decision rules remain unchanged. This change corrects token derivation to implement the existing criterion; it does not add accepted vocabulary.
- Reinterpreting or migrating historical trial leaves into the new denominator.
- Adding model-specific prompts, protocol-specific repair rules, alternative validators, quality-judge gates or a new calibration framework.
- Automatically starting recovery, qualifying a production model, freezing production configuration or supplying an owner signature.

## Decisions

### 1. Extend the existing trial engine with a declared repair mode and phase-selected prompt bundle

The calibration batch declaration will carry a lineage-bound repair mode and output-contract reference. Orthogonally, invocation depth selects one of two source files belonging to the same ArchitecturePlanner role: depth zero renders `nepa/agents/prompts/architecture_planner_initial.md`; every semantic-repair depth renders `nepa/agents/prompts/architecture_planner_repair.md`. The renderer never concatenates the initial template into a repair request and every call is fresh and history-free. Initial depth zero remains the existing ArchitectureDraft completion. For the new development protocol, depths one and two bind the patch output contract and run the same driver loop over the candidate produced by the prior depth. Existing recovery and formal qualification declarations retain their current full-draft repair mode while selecting the repair-stage template; the behavior is not inferred from prompt text or model identity.

This reuses the current invocation, trace, validation, report and provider paths while preventing the shared recovery coordinator's output semantics from being silently migrated. The seven-role catalog and `roles.architecture_planner` route remain unchanged. A separate repair role or patch-only driver was rejected because either would duplicate routing, batching, retry, accounting and evidence logic and could diverge from production validation.

The former `nepa/agents/prompts/architecture_planner.md` stops being a runtime template after both new files and their callers are in place. It is not retained as a fallback, because that would make the active phase contract dependent on failure behavior and could reintroduce mixed initial/repair instructions.

### 2. Use a closed, presence-checked JSON-Pointer patch contract

The new repair payload is a small versioned object containing an ordered `patch_ops` list. Each operation has a closed operation kind (`add`, `replace` or `remove`), an RFC 6901 JSON Pointer path, an explicit expected-presence state, and a value only for add/replace. The payload has no prior-value hash or other model-authored value digest. Additional fields and complete ArchitectureDraft-shaped responses are invalid.

The controller applies operations to an in-memory copy in listed order and publishes no candidate unless every operation satisfies its presence rule: `add` targets an absent path, while `replace` and `remove` target an existing path. Duplicate target paths, ancestor/descendant overlap within one payload, root replacement, invalid array addressing and an operation outside the allowed-path set reject the whole patch. Successful application is one atomic semantic transition; the persisted prior candidate is immutable. This contract keeps broad or inapplicable rewrites mechanically visible without requiring an LLM to calculate a cryptographic digest.

An unrestricted RFC 6902 document was rejected because it permits operations such as move/copy whose source effects complicate locality proofs. Returning a partial ArchitectureDraft subtree was rejected because merge semantics and deletion intent would be ambiguous.

### 3. Derive repairable paths and exact coupled projections from current failures

The current recovery impact policy is retained for recovery, but M1-4a2 patch mode gets a separately versioned mapping frozen into the lineage. For each canonical validator issue, an exact issue path is the preferred model-editable target. A gate-level prefix is used only when the issue has no actionable path, and wildcard expansion is resolved against the current candidate before rendering the call. The rendered repair context contains the resulting canonical, sorted concrete model-editable path set.

Layout `path` and `path_pattern` values are identities referenced by module and work-package file projections. When an admitted model patch changes one of those identities, the controller expands the old and new forms over the unchanged declared domain, derives a one-to-one old-to-new path map, and mechanically substitutes only exact matching values in `modules[].owns_files` and `work_packages[].allowed_files`. These coupled substitutions are controller-authored derived operations, not additional model authority. They are included in the same atomic candidate transition and persisted separately from the model patch. If the expansion domains differ, a mapping is ambiguous, a referenced value has no unique replacement, or another field would need to change, the whole patch is rejected rather than widening locality.

After application, the controller compares canonical before/after values and requires every changed leaf to be attributable to at least one current issue. It then reruns the ArchitectureDraft Schema and all fifteen gates, recording improvements, unchanged failures and regressions. Passing a patch does not mean passing the trial; only the complete validator determines p1 or p2.

Reusing the existing broad recovery closure unchanged was rejected because prefixes such as `/modules` or `/layout` would allow replacement of correct siblings and would not satisfy the new preservation requirement. Allowing the model to replace complete scalar arrays was also rejected because it could rewrite unrelated correct entries; deterministic exact-value projection preserves those entries.

### 3a. Make path-neutrality token derivation placeholder-aware

`arch_15` first recognizes the two Schema/design-legal placeholders `{message_id}` and `{type_id}` as atomic tokens. It also recognizes exact Spec-derived identifiers, including identifiers containing separators, before applying the existing generic tokenization to the remaining literal path and purpose text. Matching uses the frozen derived-identifier set and deterministic longest exact spans so a derived identifier cannot authorize adjacent undeclared text. The existing generic responsibility whitelist remains unchanged.

Changing the Schema to forbid `{type_id}`, adding `type` to the whitelist, or teaching the prompt to avoid a legal layout form were rejected because each would either contradict the authoritative design or weaken the neutrality gate instead of correcting its implementation.

### 4. Version patch, prompt-bundle and evidence contracts without rewriting history

The lineage manifest continues to bind the patch Schema bytes, patch applier bytes, patch-locality mapping and metric definition through the existing lineage contract. New protocol, trial request/response/validation, report, assessment, revision, selection and handoff Schemas receive a new schema version or new concrete schema file where a union would weaken validation. Historical files are not rewritten. Recompute selects the correct contract from the lineage/protocol artifact and rejects cross-version assembly.

One prompt-bundle snapshot stores the declared bundle version and the complete `initial` and `repair` source bytes using the existing snapshot publication path. Version/revision records refer to that snapshot and record which single stage changed plus its textual diff. They do not add `bundle_sha256`, `initial_prompt_sha256`, `repair_prompt_sha256`, a parent-prompt hash or any equivalent digest gate. Each actual LLM call still uses the already-existing trace prompt fields for the rendered request; this is reuse, not a second template identity mechanism.

Each repair depth persists: rendered request and response evidence, parsed patch, allowed-path proof, presence/application result, before/after candidate refs, full validation ref, locality/regression result, trace ref and usage. The trial validation index remains the canonical p0/p1/p2 summary, while leaves remain the source of truth.

Changing existing version-2 Schema constants in place was rejected because it would make retained historical artifacts fail validation or falsely validate under new semantics.

### 5. Replace only the development coordinator's old state machine and single-prompt version unit

The M1-4a2 coordinator uses `v0`～`v4`, base trial count three, semantic depth two, no extension artifact and no slot-retry exception. A version attempt declares all nine initial trial identities before Provider I/O. An infrastructure-invalid slot makes the attempt audit-only; retry creates a new complete three-slot attempt. Initial-generation accounting is computed from declared depth-zero trials only, with a per-model ceiling of 15. Repair and format calls are reported in separate usage fields.

The validator/locality correction changes lineage-controlled bytes, so the pre-fix attempts cannot be resumed or revalidated into the post-fix comparison. The post-fix lineage starts its owner-authorized per-model ceiling at zero and may consume at most 15 new initial-generation trials across its V0～V4. The experiment summary still reports pre-fix initial, format and repair usage in a separate audit section and records the authorization and reason for the reset; those calls are never silently erased or charged to a prompt version in the post-fix lineage.

V1～V4 admission continues to require one distinct evidence-backed prompt-only hypothesis and immutable parent/evidence references. Each transition names either `initial` or `repair` as its sole edit target, verifies the other stage is byte-identical to the parent bundle, and rejects cross-version pairing. The first screening pass ends development and rejects later versions; therefore 15 is a hard ceiling reached only when all five rounds execute, not a promise to spend unused calls. The four revision opportunities belong to the bundle as a whole, not to each stage separately.

Keeping N=5 extensions as an ambiguity escape hatch was rejected because the authoritative design 6.1.0 inherits the fixed N=3 rule and forbids expansion or replacement sampling.

### 6. Compute screening and fallback from p2 without weakening diagnostics

For each complete N=3 slot report, screening requires p2 successes of at least two, zero truncation and valid infrastructure. All three slots must pass independently. p0, p1, all fifteen gate states at all declared depths, patch rejection/locality and usage remain reportable diagnostics but do not become extra gates.

If no version passes through V4, the coordinator compares only complete assessments in the fixed order: minimum three-slot p2, minimum three-slot p1, minimum three-slot semantic first-pass rate, minimum three-slot Schema-after-format-repair rate, then lower total cost. An exact maximal tie produces only `PROMPT_SELECTION_TIE`; the change does not invoke recovery.

### 7. Preserve immutable publication and resume boundaries

Protocol, version, attempt and trial declarations are published before their dependent calls. Trial leaves remain immutable under the existing artifact-reference contract. A version assessment is publishable only after all three reports recompute from leaves. Selection/tie and optional handoff are published atomically only after recomputing every referenced assessment and verifying both repository prompt files byte-for-byte against the selected bundle snapshot.

Resume scans declarations and immutable leaves, continues only missing work within the same attempt when the existing driver contract permits it, and never replaces a completed trial. Infrastructure-invalid attempts remain audit evidence and require a newly numbered coherent attempt. Mutated or mismatched evidence causes a controlled failure rather than repair or overwrite.

### 8. Gate live execution behind deterministic and provenance checks

Before any Provider I/O, implementation records a design-6.1.0 experiment brief and preregistration; verifies the current design revision and relevant requirements, M1-4a1 components through their existing contracts, gold inputs, three logical slot configurations, context preflight, both prompt files' neutrality, patch neutrality and environment-variable presence; and passes the focused fake-provider suite. It also proves depth zero selects only `initial`, both repair depths select only `repair`, all three slots receive the same bundle bytes and each revision changes one stage. No secret value is read into tracked artifacts, and no new SHA-256/hash computation or validation field is added for these checks.

Live execution then uses the existing calibration runner with commands narrowed to initialization, one version attempt, evidence-backed prompt revision, recomputation and reporting. Results are generated from the new lineage leaves. Old experiment directories and lineages are checked for unchanged hashes before closure.

## Risks / Trade-offs

- **[N=3 makes p2 coarse: 0.60 means 2/3]** → Report exact k/N and all diagnostics, select only under the preregistered rule, and retain M1-4a3 N=10 as the qualification boundary.
- **[Patch paths may be too broad or too narrow]** → Prefer exact validator issue paths, freeze the mapping into lineage, test every gate with positive and out-of-scope patches, and reject instead of broadening at runtime.
- **[Mechanical path projection could update an unrelated equal string]** → Restrict projection to the two declared reference collections, require membership in the changed layout slot's old expansion, persist every derived substitution, and reject ambiguous closure.
- **[Budget reset could hide already incurred usage]** → Keep all pre-fix calls and cost in a separate immutable audit section while making the new 15-trial ceiling explicitly scoped to the post-fix lineage.
- **[Array edits can be order-sensitive]** → Enforce operation-specific presence, reject overlapping operations, apply to a copy and validate the complete result before publishing.
- **[A second patch can regress a gate fixed by the first]** → Recompute all fifteen gates after each depth and retain p1→p2 regression evidence; a trial passes only on a complete validator pass.
- **[Two templates could drift into two roles or model-specific variants]** → Keep one registry entry and route, require both files in every bundle snapshot, scan both for protocol/model/provider branches and reject cross-version pairing.
- **[A revision could hide two simultaneous prompt changes]** → Record the selected stage and textual diff, compare the other stage byte-for-byte to the parent bundle and reject before Provider I/O; do not add hash fields for this comparison.
- **[Shared driver changes could alter recovery/formal calibration]** → Make repair mode explicit in the declaration, use the repair-stage template without changing those protocols' full-draft output semantics, and run existing recovery/calibration regression suites.
- **[Provider failures can consume cost without comparable evidence]** → Predeclare attempts, retain invalid evidence, rerun coherent three-slot N=3 attempts only, and keep initial versus repair usage separate.
- **[Selected p2=2/3 prompt may still be weak]** → Limit the result to an M1-4a3 admission handoff and make no production claim.

## Migration Plan

1. Record the current design revision, M1-4a1 implementation references, gold inputs and existing historical evidence; create or amend the implementation brief and preregistration without Provider calls and without introducing new hash checks.
2. Split `architecture_planner.md` into `architecture_planner_initial.md` and `architecture_planner_repair.md`; route by invocation depth through the existing ArchitecturePlanner role, and remove the old file from runtime selection without retaining a fallback.
3. Add the patch contract, deterministic application/locality evidence and conditional Agent output binding; update trial artifacts and recomputation while keeping full-draft declarations working for recovery/formal protocols through the repair-stage template.
4. Replace the development coordinator and development-only Schemas with V0～V4/N=3/depth-two/p2 behavior and a two-file bundle version unit; each revision changes one stage and uses no new bundle/template digest fields. Remove extension and slot-retry actions from the current M1-4a2 protocol surface.
5. Pass focused fake-provider, Schema/example, two-template selection/neutrality, recovery-regression and full repository validation.
6. Correct placeholder/derived-identifier token derivation and exact coupled path projection; pass focused positive, negative, ambiguity, preservation and historical-regression tests.
7. Preserve all pre-fix and single-template lineages as audit-only, record the owner-authorized budget reset, initialize a fresh design-6.1.0 post-fix lineage with zero admitted trials and a 15-initial-trial ceiling per model, and rerun the complete pre-live gate.
8. Run the bounded live workflow from post-fix V0. Stop on first pass or after V4 fallback/tie, then recompute and atomically publish the machine summary, report and permitted terminal artifact exclusively from post-fix leaves.
9. Verify historical evidence remains unchanged and scope limits hold. If implementation or deterministic validation fails before live work, make no Provider calls. If live evidence is incomplete, retain it as audit-only and publish no selection over it.

Rollback before live execution is ordinary source reversion with no calibration state to migrate. After live execution, source rollback does not delete or reinterpret the new immutable lineage; its evidence remains non-consumable unless it validates under the exact recorded implementation and protocol hashes.
