## ADDED Requirements

### Requirement: The controller uses one deterministic candidate-completion path
After either strategy produces a complete semantic draft, the controller SHALL normalize it to the existing PlanDraftIR, run the existing deterministic Linker and Delivery Blueprint compiler, inject only controller-owned frozen refs, run full plan lint across S4-G0 through S4-G6, and preserve the resulting link/lint evidence. A candidate SHALL be critic-eligible only after this complete path returns zero errors, and publishable only after a subsequent PlanCritic pass and one final complete deterministic recomputation. No controller branch SHALL maintain a second dependency, coverage, Blueprint, or lint implementation. (Design 7.1.1: §6.4.5-§6.4.7; pipeline design 1.2.0 §5.3; M1-4c.)

#### Scenario: Layered and flat drafts are semantically equal
- **WHEN** both strategies supply equivalent normalized architecture, work packages, and local task shards
- **THEN** the common completion path produces byte-identical candidate Plan, Blueprint, coverage, and link evidence

#### Scenario: Basic lint passes but full input is absent
- **WHEN** a candidate passes basic shape checks but cannot reconstruct its constraints, Blueprint, Target Profile, or budget inputs
- **THEN** it is not critic-eligible or publishable

### Requirement: Critic results are validated and deterministically routed
The controller SHALL recompute a critic verdict from the validated issue list: any blocker or major requires `revise`, while `pass` is legal only with no blocker or major. Mechanical issues SHALL be corrected only by deterministic recomputation when the source semantics are already valid. In layered mode a task/work-package-local semantic issue SHALL invalidate only the named shard and a global architecture issue SHALL invalidate the architecture and all child shards; in flat mode any semantic revise SHALL invalidate the complete flat draft. After repair, all deterministic completion gates and a fresh critic invocation SHALL rerun. (Design 7.1.1: §6.4.6; M1-4c.)

#### Scenario: Critic verdict contradicts its issues
- **WHEN** a critic reports `pass` while its issue list contains a blocker or major
- **THEN** the controller treats the review as invalid and does not publish the candidate

#### Scenario: Critic returns only minor issues
- **WHEN** the issue list contains no blocker or major and the verdict is `pass`
- **THEN** the controller normalizes the unresolved minor issues into final Plan review while preserving the complete review history only in `_s4`

### Requirement: Candidate completion remains state-free and M1-4d-free
The M1-4c completion and critic loops SHALL preserve the existing state-free Plan and PlanDraftIR contracts. They SHALL NOT calculate or inject task uid, obligation digest, guidance digest, migration classification, revision entry, execution status, attempts, evidence, workspace state, or task test acceptance before M2-0. Those fields SHALL not affect Blueprint semantics or initial publication. (Design 7.1.1: §5.2, §6.4.5-§6.4.7, §10.2 M1-4c/M1-4d; M1-4c.)

#### Scenario: A repaired candidate is relinked
- **WHEN** an admitted semantic repair changes architecture or a shard
- **THEN** the recomputed Plan still contains only the current M1-4b-derived static fields and no M1-4d or execution data
