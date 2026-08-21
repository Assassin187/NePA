## Purpose

Define the bounded, evidence-driven workflow that develops and selects one shared protocol-neutral ArchitecturePlanner prompt for later M1-4a3 qualification without changing any lineage-controlled planning behavior.

## ADDED Requirements

### Requirement: Every candidate version is one shared protocol-neutral prompt
For each V0, V1, or V2 candidate, the system SHALL send byte-identical raw ArchitecturePlanner template bytes to the configured Qwen and DeepSeek batches. The shared template SHALL contain no protocol-specific name, message or field, REQ id, path, interface constant, port, topic/subscription semantic, protocol-identity branch, model/provider name, model-specific conditional, or model-specific few-shot example. Prompt changes SHALL be limited to generic responsibilities, constraint expression, output ordering, self-check instructions, and protocol-neutral abstract examples; concrete protocol facts SHALL enter only through the frozen inputs. (Design: §6.4, §6.4.8.2, §8.8, D1.11.)

#### Scenario: A version is dispatched to two models
- **WHEN** one development version is admitted for trial execution
- **THEN** both model batches bind the same lineage id, logical prompt version, raw-template SHA-256, frozen inputs, Schema, validator, serializer, call shape, and model-specific configurations already frozen by that lineage

#### Scenario: A candidate adds model- or protocol-specific guidance
- **WHEN** the raw shared template contains a prohibited target-protocol constant, protocol branch, model/provider name, or model-specific condition/example
- **THEN** the neutrality gate rejects the version before any provider call or development record is published

#### Scenario: Returned model identity drifts during development
- **WHEN** a configured candidate returns a different full provider/model/version identity or parameter-support behavior between development versions or sample segments
- **THEN** the prompt-only experiment is invalidated and no screening or selection result is published from the mixed evidence

### Requirement: Live development initialization uses explicit complete calibration inputs
Before any provider I/O, development initialization SHALL consume an explicit calibration configuration artifact and explicit context-window declarations for exactly Qwen and DeepSeek. It SHALL resolve and validate each provider, requested model, request parameters, pricing entry, and positive context-window limit; each request SHALL use `max_tokens = 65536`, and SHALL bind their canonical values and source hashes to the lineage and development protocol. The resolved configuration SHALL map Qwen and DeepSeek `api_key_env` respectively and exactly to `NEPA_QWEN_API_KEY` and `NEPA_DS_API_KEY`; callers SHALL NOT supply alternate variable names. Preflight SHALL read only the current process environment and SHALL require both fixed variables to exist and be non-empty before provider I/O; it SHALL NOT read or parse `.bashrc`, `.profile`, dotenv files, or any other shell configuration. It SHALL NOT infer a context limit, price, provider, model, or parameter from a model name, provider response, or network lookup. Secret values SHALL NOT be accepted through CLI arguments or configuration files and SHALL NOT be serialized, hashed into evidence, traced, logged, copied into any development artifact, or included in error text. Lineage and protocol SHALL record only the fixed environment-variable names, never values or value hashes. (Design: §6.4.8.1-§6.4.8.2, §8.3.)

#### Scenario: Explicit calibration initialization succeeds
- **WHEN** the supplied configuration resolves exactly the two candidates with valid providers, models, parameters including `max_tokens = 65536`, pricing, the fixed §8.3 `api_key_env` mapping, and positive explicit context limits, and both fixed variables are non-empty in the current process environment
- **THEN** initialization publishes lineage/protocol evidence binding the canonical non-secret projections before permitting a provider call

#### Scenario: Pricing is missing
- **WHEN** any configured candidate lacks the exact provider/model pricing entry required by the resolved request configuration
- **THEN** initialization fails before lineage/protocol publication or provider I/O

#### Scenario: A context-window declaration is missing or invalid
- **WHEN** any of the two candidates has no explicit positive integer context-window limit or the declaration contains a missing/extra model id
- **THEN** initialization fails without inferring a limit from the model name or network

#### Scenario: Resolved configuration drifts after initialization
- **WHEN** the configuration, pricing, provider/model/parameter projection, or context-window declarations no longer match their protocol-bound hashes before a version or attempt starts
- **THEN** the operation fails before provider I/O and no evidence from the drifted configuration enters the development sequence

#### Scenario: A required fixed environment variable is absent or empty
- **WHEN** either `NEPA_QWEN_API_KEY` or `NEPA_DS_API_KEY` is missing or empty in the current process environment
- **THEN** preflight fails before provider I/O and identifies only the missing environment-variable name without exposing any secret value

#### Scenario: Configuration declares a non-authorized key variable
- **WHEN** any provider configuration maps `api_key_env` to a name other than its fixed §8.3 value
- **THEN** initialization fails before provider I/O even if that alternate variable is present

#### Scenario: A secret is available to the provider
- **WHEN** both fixed variables contain sentinel credential values used for live-call setup
- **THEN** only the fixed environment-variable names are represented in lineage/protocol evidence and no sentinel value or value hash appears in CLI/config/JSON artifacts, lineage, protocol, trace, logs, diagnostics, or exception text

#### Scenario: A shell configuration contains a key
- **WHEN** a fixed variable is absent from the current process environment but appears in `.bashrc`, `.profile`, a dotenv file, or another shell configuration
- **THEN** preflight reports the fixed variable as missing without reading or parsing that file

### Requirement: Admitted prompt snapshots are immutable execution inputs
Before admitting V0, V1, or V2, the workflow SHALL publish the candidate raw template bytes immutably as `versions/<version>/prompt.md` and bind one relative prompt ref/SHA-256 into the version and every two-model batch. Every provider request for that version SHALL render from those exact snapshot bytes rather than dynamically loading the mutable repository template. The repository source, immutable snapshot, batch raw-template hash, rendered trace identity, and actual provider request SHALL remain consistent; any mismatch or source drift SHALL stop further provider I/O and SHALL prevent a valid assessment from being published. Snapshotting SHALL NOT change the ArchitecturePlanner inputs, output contract, system instruction, or call shape. (Design: §6.4, §6.4.8.1-§6.4.8.2, §8.8.)

#### Scenario: Two models consume one admitted snapshot
- **WHEN** one version attempt dispatches Qwen and DeepSeek
- **THEN** both workers render from the same immutable prompt ref and record the same raw-template SHA-256 in their batches and traces

#### Scenario: Source and snapshot disagree before a call
- **WHEN** the repository ArchitecturePlanner template or proposed source hash differs from the admitted snapshot before initial or repair provider I/O
- **THEN** the call is rejected before network activity and the attempt cannot become valid

#### Scenario: The snapshot is mutated or substituted
- **WHEN** the prompt snapshot bytes, path, ref, or SHA-256 no longer match the version record
- **THEN** admission, resume, recomputation, and assessment fail without falling back to the repository template

#### Scenario: Source changes during an executing attempt
- **WHEN** repository prompt bytes drift after attempt admission but before both batches and their assessment commit
- **THEN** no further provider call is admitted after detection, existing evidence remains auditable, and no valid assessment or selection can consume that attempt

### Requirement: Prompt development follows the bounded V0/V1/optional-V2 sequence
The development protocol SHALL begin with V0 and SHALL admit at most V0, V1, and V2 in that order. V0 SHALL run exactly five independent trials per configured model with one semantic-repair allowance. V1 SHALL be admitted only after complete, recomputable V0 evidence and one recorded evidence-backed prompt-defect hypothesis. V2 SHALL be admitted only when the final V1 assessment does not pass the screening gate and its evidence supports one second distinct hypothesis. No version SHALL be skipped, repeated under different bytes, added after V2, or edited after it becomes the selected candidate. (Design: §6.4.8.2.)

#### Scenario: V0 starts
- **WHEN** no development version exists for the controlled lineage
- **THEN** only V0 with N=5 and `semantic_depth=1` can be admitted

#### Scenario: V1 is proposed without complete V0 evidence
- **WHEN** any V0 model report is absent, incomplete, infrastructure-invalid, non-recomputable, or from a different lineage/prompt
- **THEN** V1 admission fails without publishing a revision or issuing a provider call

#### Scenario: A third prompt edit is proposed
- **WHEN** V2 already exists and another prompt byte change is proposed
- **THEN** development stops with a bounded-protocol failure and does not create V3

#### Scenario: A selected version is edited
- **WHEN** a version has already been selected for M1-4a3 and any further ArchitecturePlanner template change is proposed
- **THEN** the existing development result is invalidated and the change cannot represent the edit as part of the completed bounded sequence

### Requirement: Every base N=5 version uses one coherent two-model attempt
Each V0, V1, or V2 base N=5 execution SHALL have an explicit monotonically numbered attempt identity and attempt state binding the version, prompt ref/hash, lineage, batch protocol, and exactly one Qwen model root and one DeepSeek model root. A version assessment SHALL consume two complete reports from the same valid attempt. If any model becomes `infrastructure-invalid`, the whole version attempt SHALL become permanently invalid for assessment and selection; a retry SHALL create a new attempt-qualified two-model batch with the same version, prompt hash, lineage, and batch protocol. Invalid attempts SHALL remain immutable audit evidence, and reports or trials SHALL NOT be completed, replaced, or combined across attempts. Reconciliation SHALL expose only the unique legal current-attempt resume or next fresh attempt. (Design: §6.4.8.1-§6.4.8.2.)

#### Scenario: One model makes a base attempt infrastructure-invalid
- **WHEN** any model in a V0, V1, or V2 N=5 attempt exhausts transport retries without a model response
- **THEN** that attempt is marked infrastructure-invalid as a two-model unit and none of its reports enters assessment or selection

#### Scenario: A base version is retried
- **WHEN** the prior attempt is durably infrastructure-invalid and the version is still admissible
- **THEN** the workflow creates the next attempt id and reruns both models with the same lineage, version, prompt hash, and N=5/depth-one protocol

#### Scenario: Reports from different attempts are presented together
- **WHEN** an assessment references model reports or trials from more than one attempt or only replaces the failed model
- **THEN** assessment recomputation fails as cross-attempt evidence and publishes no result

#### Scenario: Resume follows an invalid attempt
- **WHEN** reconciliation finds one committed infrastructure-invalid attempt and no later committed attempt for an otherwise admissible version
- **THEN** the only legal next action is creation of the next complete two-model attempt, never resumption or repair of the invalid attempt

#### Scenario: Resume finds an incomplete non-invalid attempt
- **WHEN** a version attempt has no infrastructure-invalid model and contains a uniquely reconcilable set of committed trials
- **THEN** resume continues only that attempt under its original two-model bindings and does not create or splice another attempt

### Requirement: N=10 expansion is evidence-gated and preserves the first five trials
V1 or V2 SHALL initially be assessed at N=5. The workflow SHALL permit expansion to N=10 only when the N=5 evidence records that the conclusion changes under a leave-one-trial-out check or that the declared screening metrics give conflicting pass/fail indications. Expansion SHALL keep the same prompt bytes and lineage, append exactly five new independent trials per model, retain the original five committed trials by reference, and produce one recomputable N=10 assessment with no duplicate or replacement sample. Expansion SHALL NOT count as another prompt version or permit a prompt edit between the first and second five trials. (Design: §6.4.8.2.)

#### Scenario: An ambiguous V1 result is expanded
- **WHEN** complete V1 N=5 evidence contains an exact qualifying ambiguity reason
- **THEN** the workflow admits exactly trials 006-010 for both models under the same V1 prompt hash and publishes an N=10 assessment over trials 001-010

#### Scenario: An unambiguous version requests more samples
- **WHEN** V1 or V2 N=5 has a stable screening conclusion and no conflicting screening metrics
- **THEN** an N=10 expansion is rejected as open-ended sampling

#### Scenario: Expansion attempts to replace a failed trial
- **WHEN** an extension omits, duplicates, mutates, or substitutes any committed N=5 trial
- **THEN** recomputation fails and no N=10 assessment is published

### Requirement: Every prompt edit has one immutable evidence-backed revision record
Each V1 or V2 edit SHALL publish an immutable record binding the previous version and prompt SHA-256, exact source report and failing-trial references, one testable prompt-defect hypothesis, one exact prompt diff, the expected affected `arch_01`-`arch_10` gates or Schema metric, the new prompt SHA-256, and the post-run stopping conclusion. A revision SHALL NOT alter the ArchitectureDraft Schema/example, `ARCH_VALIDATE`, serializer, frozen inputs, planning/Delivery construction, model configurations, call parameters, metric definitions, or any non-prompt lineage component. (Design: §6.4.8.1-§6.4.8.2.)

#### Scenario: One focused prompt revision is recorded
- **WHEN** complete prior-version evidence supports one prompt-defect hypothesis and the proposed diff changes only the shared template
- **THEN** the revision record is accepted with hash-bound evidence, one hypothesis, the exact diff, expected gate/metric effect, and the new prompt hash

#### Scenario: A revision changes a controlled component
- **WHEN** a revision also changes a Schema, validator, serializer, input constructor, Delivery compiler, model configuration, call shape, parameter, or metric definition
- **THEN** it cannot be admitted as a prompt-only revision under the existing lineage

#### Scenario: A revision bundles two hypotheses
- **WHEN** one V1 or V2 record claims multiple independent prompt defects or combines unrelated prompt changes
- **THEN** the record is rejected before the new version is run

### Requirement: Screening is per model and stops at the first passing version
A version's final N=5 or authorized N=10 assessment SHALL pass the development screening gate only when Qwen and DeepSeek each independently have `schema_after_format_repair_rate = 1.00`, cumulative `p1 = 1.00`, `arch_semantic_first_pass_rate >= 0.80`, zero truncations, no infrastructure-invalid batch, and no same `arch_01`-`arch_10` gate failing on the first Schema-valid candidate in at least two trials for that model/version. The threshold of at least two trials is the explicitly authorized M1-4a2 interpretation of “repeated systematic hard-gate failure.” A version with incomplete or invalid evidence SHALL not receive a screening result. The first version whose final assessment passes SHALL be selected immediately and SHALL prevent all later prompt edits and samples. (Design: §6.4.8.2.)

#### Scenario: All two models pass independently
- **WHEN** one final version assessment satisfies every screening condition separately for each model
- **THEN** that version is selected as the sole M1-4a3 prompt candidate and development stops

#### Scenario: A combined average hides one model failure
- **WHEN** an aggregate across models would pass but any individual model misses a screening condition
- **THEN** the version fails screening and cannot be selected by the aggregate

#### Scenario: A hard gate fails repeatedly before repair
- **WHEN** the same architecture gate fails on the first Schema-valid candidate in at least two trials for one model even though semantic repair raises cumulative `p1`
- **THEN** that model and version fail the systematic-hard-gate condition

### Requirement: V2 fallback selection follows the fixed lexicographic rule
If and only if V2 has a complete final assessment and no version passed screening, the system SHALL choose exactly one M1-4a3 prompt candidate by comparing each version's final assessment in this order: maximize the minimum per-model cumulative `p1`; then maximize the minimum per-model `arch_semantic_first_pass_rate`; then maximize the minimum per-model `schema_after_format_repair_rate`; then minimize total cost across the two models. It SHALL NOT use averages, post-hoc weights, hidden quality judgments, or additional samples. An exact tie after total cost SHALL stop with an explicit selection-tie failure rather than silently invent an extra criterion. (Design: §6.4.8.2.)

#### Scenario: No version passes by the end of V2
- **WHEN** V0, V1, and V2 have complete final assessments and all fail screening
- **THEN** the workflow publishes the uniquely ranked version and the exact comparison tuple as the M1-4a3 candidate

#### Scenario: Two versions remain exactly tied
- **WHEN** all four authorized comparison values are equal for more than one version
- **THEN** selection fails with a machine-readable tie and M1-4a3 remains blocked pending an authoritative decision

### Requirement: Development evidence is immutable, recomputable, and not production qualification
The workflow SHALL store canonical protocol, prompt snapshot, version-attempt declaration/outcome, revision, extension, per-version assessment, and final-selection records below the existing gitignored calibration lineage root. Every record SHALL bind its input reports and prompt/configuration artifacts by relative path and SHA-256, publish atomically, reject mutation or cross-lineage/cross-version/cross-model/cross-attempt substitution, and reproduce byte-identically from the underlying M1-4a1 evidence. Completion SHALL require the two configured models' complete V0 and every conditionally required V1/V2 batch; fake-provider results SHALL validate implementation only and SHALL NOT satisfy development completion. These records SHALL NOT qualify a production model, satisfy M1-4a3 B1-B4, create an owner signature, or produce a formal Run/S4/Plan/Blueprint/report artifact. (Design: §6.4.8.2-§6.4.8.3, §9.1.5, D1.0.)

#### Scenario: A development assessment is recomputed
- **WHEN** all referenced lineage, prompt, batch, report, trial, trace, revision, and extension evidence remains valid
- **THEN** recomputation produces byte-identical assessment and selection bytes

#### Scenario: Only fake-provider batches exist
- **WHEN** all deterministic tests pass but no complete evidence from the configured Qwen and DeepSeek candidates exists
- **THEN** the implementation is testable but M1-4a2 remains incomplete and M1-4a3 is not admitted

#### Scenario: A selected prompt is handed to M1-4a3
- **WHEN** bounded development completes with one selected candidate
- **THEN** the handoff identifies the lineage id, logical version, raw prompt SHA-256, final sample assessment, and selection reason while making no production-model, repair-budget, call-shape, or owner-approval decision
