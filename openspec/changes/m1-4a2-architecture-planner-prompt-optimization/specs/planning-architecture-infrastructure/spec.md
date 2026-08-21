## MODIFIED Requirements

### Requirement: Lineage identity freezes every non-prompt comparison variable
The system SHALL derive a `lineage_id` from a canonical lineage manifest that binds the frozen input references, planning-index and Delivery-Constraints construction, ArchitectureDraft Schema, canonical serializer, `ARCH_VALIDATE`, the explicit resolved provider configurations and pricing projections, exactly the configured Qwen/DeepSeek requested model/parameter configurations, including `max_tokens = 65536` for both and context-window declarations, the §8.3 fixed API-key environment-variable names (`NEPA_QWEN_API_KEY`, `NEPA_DS_API_KEY`), and calibration metric definitions. These non-secret calibration inputs SHALL be validated before provider I/O and SHALL NOT be inferred from model names or the network; secret values and their hashes SHALL remain outside lineage and persisted evidence. The shared ArchitecturePlanner prompt hash SHALL be recorded by each prompt version but SHALL NOT be part of `lineage_id`, because it is the only model-input variable M1-4a2 may change within a lineage. Batch-protocol controls including `trial_count` and `semantic_depth` SHALL be excluded from `lineage_id` so that the prescribed N=5/N=10 development batches and later N=20 qualification batches can reuse the same controlled lineage; they SHALL instead be mandatory immutable fields in each batch and in any development or qualification record that consumes that batch. Reports with different batch-protocol controls SHALL NOT aggregate their trials into one denominator or masquerade as one batch result. Any bound component, resolved provider/pricing/model/parameter/context projection, authorized key-variable-name mapping, or metric-definition change SHALL produce a different lineage id; evidence from different lineages SHALL NOT be aggregated or compared as one prompt-only experiment. (Design: §6.4.8.1-§6.4.8.3, §8.3, §9.2; authorized M1-4a2 lineage-boundary clarification.)

#### Scenario: Only the shared prompt changes
- **WHEN** a later prompt version changes prompt bytes while every lineage-bound component remains identical
- **THEN** it has a new prompt hash/version under the same lineage id

#### Scenario: A development batch expands its sample count
- **WHEN** one prompt version follows the authorized development protocol from N=5 to N=10 without changing any lineage-bound component or the prompt bytes
- **THEN** both immutable batch declarations retain the same lineage id and distinct trial-count evidence, and their trials are combined only by the explicit N=10 development-extension contract

#### Scenario: Qualification changes the allowed semantic depth
- **WHEN** M1-4a3 later reuses the selected prompt and controlled components with its prescribed N=20 and two-repair batch protocol
- **THEN** the qualification batch retains the same lineage id while recording its own immutable `trial_count=20` and `semantic_depth=2`

#### Scenario: A controlled component changes
- **WHEN** an input, Schema, validator, serializer, input constructor, Delivery Constraints compiler, resolved provider/pricing/model/parameter/context projection, or metric definition changes
- **THEN** a new lineage id is required and prior trials cannot enter or be compared as part of the new prompt-only experiment

#### Scenario: Calibration configuration is incomplete
- **WHEN** any selected model lacks an explicit provider/model/parameter projection, matching pricing entry, or positive context-window declaration
- **THEN** lineage publication and provider I/O are rejected rather than filling the missing value by inference

#### Scenario: Incompatible batch protocols are presented as one report
- **WHEN** a caller attempts to merge trials from different `trial_count` or `semantic_depth` declarations without the exact development-extension or later qualification contract that references those immutable batches
- **THEN** aggregation fails before publishing any combined metric

#### Scenario: A prompt label is reused for different bytes
- **WHEN** a prompt-version label already exists under a lineage with a different prompt SHA-256
- **THEN** publication fails as an artifact conflict rather than overwriting or mixing evidence
