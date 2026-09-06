## ADDED Requirements

### Requirement: Production S6 coding roles use one closed protocol-neutral contract
Coder and Fixer SHALL be invocable in S6 with the shared closed output object containing `micro_plan`, a non-empty array of unique full-file `{path,content}` objects, and `notes`. Coder SHALL receive the exact task/work-package card, architecture and contract summaries, required Spec slice, internal contract-map/interface contents, language guidance and current task-owned files. Fixer SHALL receive the same required inputs plus the current execution mode and latest matching failed candidate, validation feedback and available diagnosis. The renderer SHALL preserve the fixed input order, SHALL reject missing or extra context, and SHALL not expose task uid/digests, unrelated Spec, original documents, test implementations/oracles, another task's history, protocol constants from template source, or plan-changing authority. (Design: §4.5, §6.6.2-§6.6.3, §8.8; D1.6/D1.8/D1.11; M1-6.)

#### Scenario: Coder context is complete
- **WHEN** S6 assembles a first-attempt context whose required sections fit the frozen context budget
- **THEN** the Coder prompt contains every required section in order and validates one full-file output object

#### Scenario: Fixer receives the matching failure
- **WHEN** a later attempt follows a persisted candidate validation failure
- **THEN** the Fixer prompt includes that candidate and its matching build/smoke feedback rather than relying on conversation history

#### Scenario: Required context exceeds the limit
- **WHEN** required task files, Spec slice, contracts and failure feedback exceed `coder_context_max_tokens`
- **THEN** invocation fails before provider I/O instead of dropping a required section

#### Scenario: Model proposes macro-plan or out-of-contract output
- **WHEN** Coder or Fixer returns a diff, empty file list, undeclared field or plan-level instruction instead of the closed full-file response
- **THEN** structured-output validation rejects the response and no plan or workspace fact is accepted from it

#### Scenario: Shared coding assets are scanned
- **WHEN** protocol-neutrality validation examines the Coder/Fixer templates and context assembler
- **THEN** no target-protocol, provider or model-specific branch is present outside explicitly injected inputs
