## MODIFIED Requirements

### Requirement: Production S6 coding roles use one closed protocol-neutral contract
Coder and Fixer SHALL be invocable in S6 with the shared closed output object containing `micro_plan`, a non-empty array of unique full-file `{path,content}` objects, and `notes`. Coder SHALL receive the exact task/work-package card, architecture and contract summaries, required Spec slice, internal contract-map/interface contents, language guidance and current task-owned files. Fixer SHALL receive the same required inputs plus the current execution mode and latest matching failed candidate, validation feedback and available diagnosis. For an accepted F1 lease only, the Fixer context SHALL additionally identify the lease authorization and exact external paths and include only those neighbor-file bytes; those paths become output-eligible only for that invocation. The renderer SHALL preserve a fixed input order, SHALL reject missing or extra context, and SHALL not expose task uid/digests except the lease identities required by the controller, unrelated Spec, original documents, test implementations/oracles, another task's history, unrelated neighbor files, protocol constants from template source, or plan-changing authority. (Design: §4.5, §6.6.2-§6.6.3, §8.8; pipeline §7.2; D1.6/D1.8/D1.11; M1-6/M1-7.)

#### Scenario: Coder context is complete
- **WHEN** S6 assembles a first-attempt context whose required sections fit the frozen context budget
- **THEN** the Coder prompt contains every required section in order and validates one full-file output object

#### Scenario: Fixer receives the matching failure
- **WHEN** a later attempt follows a persisted candidate validation failure
- **THEN** the Fixer prompt includes that candidate and its matching build/smoke feedback rather than relying on conversation history

#### Scenario: F1 Fixer receives authorized neighbor files
- **WHEN** the controller invokes Fixer for a persisted accepted lease
- **THEN** its context contains the exact lease scope and leased file bytes, and its output may name only current-owned files plus those exact paths

#### Scenario: Required context exceeds the limit
- **WHEN** required task files, Spec slice, contracts, failure feedback and any authorized leased files exceed `coder_context_max_tokens`
- **THEN** invocation fails before provider I/O instead of dropping a required section or widening visibility

#### Scenario: Model proposes macro-plan or out-of-contract output
- **WHEN** Coder or Fixer returns a diff, empty file list, undeclared field or plan-level instruction instead of the closed full-file response
- **THEN** structured-output validation rejects the response and no plan or workspace fact is accepted from it

#### Scenario: Shared coding assets are scanned
- **WHEN** protocol-neutrality validation examines the Coder/Fixer templates and context assembler
- **THEN** no target-protocol, provider or model-specific branch is present outside explicitly injected inputs
