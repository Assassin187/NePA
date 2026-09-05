## MODIFIED Requirements

### Requirement: ArchitectureDraft is a closed production contract
The system SHALL provide one draft-2020-12 ArchitectureDraft Schema, one conforming minimal example, and canonical serialization. A draft SHALL contain only architecture decisions and their context references, explicit assumptions, modules with responsibilities/non-goals/file and contract boundaries, internal contracts with owner/readiness/provider/consumer information and a non-empty closed `exports[]` set, and work-package skeletons with goals, allowed files, contract sets, dependencies, acceptance outcome, context references, and primary/supporting requirement responsibilities. Each export SHALL contain exactly `interface_file`, `symbol`, and implementation-free `signature`; its file SHALL belong to the declaring contract, its symbol SHALL be mechanically attributable to frozen inputs and naming rules, and `(interface_file, symbol)` SHALL be unique within the contract. The draft SHALL NOT contain final `T-###` ids, task instructions, task shards, input or Blueprint hashes, coverage, review, run state, Plan State, S5 file contents, or coder prompts. (Design 7.2.0: §5.2.1-§5.2.2, §5.6.5.2, §6.4, §6.4.4, §6.4.8.1; pipeline design 1.3.0 §3.2, §5.2.2; M1-4d.)

#### Scenario: A complete architecture draft is serialized
- **WHEN** a draft satisfies the production ArchitectureDraft Schema including every contract export
- **THEN** repeated canonical serialization produces identical bytes that validate against the same Schema

#### Scenario: An export is not closed
- **WHEN** an export names a file outside its contract, repeats an `(interface_file, symbol)` pair, uses a symbol not derivable from frozen naming inputs, omits a signature, or carries implementation content
- **THEN** Schema or semantic architecture validation rejects the response before it can be linked

#### Scenario: A downstream-only field is returned
- **WHEN** an ArchitecturePlanner response includes a task id, task instructions, coverage, runtime status, input hash, Blueprint hash, or other forbidden downstream field
- **THEN** Schema validation rejects the response before semantic architecture validation

#### Scenario: The production contract is bound to ArchitecturePlanner
- **WHEN** an M1-4a trial or production S4 invokes the registered ArchitecturePlanner role
- **THEN** the invocation uses this Schema and its conforming example through the existing M1-3 output-contract slot

### Requirement: ARCH_VALIDATE is the single production S4-G2 validator
The system SHALL expose one deterministic `ARCH_VALIDATE` result over a Schema-valid ArchitectureDraft, frozen planning index, Test Manifest metadata, and Delivery Constraints. It SHALL evaluate the complete S4-G2 contract under stable gates `arch_01` through `arch_15`, including identifier/reference integrity; module boundaries; contract owner/readiness/interface/export conditions; unique provider and declared consumers; module and work-package projections; exact contract-derived dependencies and DAG closure; requirement ownership/readiness; and the complete free-layout safety/class/build/layering/neutrality gates. Export validation SHALL use the same mechanically derived naming set used by production constraints and SHALL NOT infer or repair a signature. The validator SHALL return every evaluable gate result and a canonically ordered issue list rather than stop at the first semantic failure. Calibration and production S4 SHALL call this same validator and SHALL NOT maintain a looser experimental validator. (Design 7.2.0: §5.2.1-§5.2.3, §5.6.5.2, §6.4.4, §6.4.8.1; pipeline design 1.3.0 §3.2, §5.2.4; M1-4d.)

#### Scenario: A valid architecture is checked twice
- **WHEN** the same Schema-valid draft and parent artifacts are validated twice
- **THEN** both results contain passing `arch_01` through `arch_15` gates and byte-identical canonical validation evidence

#### Scenario: An export symbol is not attributable
- **WHEN** a contract export symbol is absent from the naming values mechanically derivable from the frozen Spec and Target Profile
- **THEN** validation fails with stable contract/export path evidence and no protocol-specific exception

#### Scenario: Multiple independent semantic defects exist
- **WHEN** a draft violates more than one evaluable S4-G2 condition
- **THEN** the result is `fail` and contains stable gate ids plus exact code/path/message evidence for every detected defect in canonical order

#### Scenario: An experiment attempts to substitute a validator
- **WHEN** a calibration caller proposes a separately configured or weakened semantic validator
- **THEN** the trial is rejected before model output can be counted in a lineage report
