## ADDED Requirements

### Requirement: Production S4 admits only the approved ArchitecturePlanner bundle
Before the first production ArchitecturePlanner invocation, the controller SHALL validate the M1-4a2 handoff, selection, assessment, owner-approval, and bundle references within their recorded lineage root. The handoff SHALL name consumer `m1-4c` and assert the recorded baseline, protocol-neutrality, and owner-signature gates. The selected `initial` and `repair` raw-template hashes SHALL equal both the bundle snapshot and the packaged production templates used by the existing ArchitecturePlanner role. Missing, drifting, unapproved, or wrong-consumer evidence SHALL stop S4 before provider I/O. This admission SHALL not claim production quality or require a new owner signature. (Design 7.1.1: §6.4.8.2, §10.2 M1-4a2/M1-4c; D1.0.)

#### Scenario: Approved bundle is admitted
- **WHEN** every handoff reference verifies and the selected raw templates are byte-identical to the packaged production pair
- **THEN** production S4 may render the existing ArchitecturePlanner role with those templates and its configured route

#### Scenario: Packaged prompt drifts
- **WHEN** either packaged raw template hash differs from the approved snapshot
- **THEN** S4 stops before rendering or provider I/O and does not substitute another calibration lineage or prompt

### Requirement: Production and calibration share architecture preparation and validation
Production S4 SHALL derive its planning index and Delivery Constraints through the same deterministic functions used by calibration, invoke the same ArchitecturePlanner initial/repair contract binding, apply semantic patches through the same path/locality/application rules, and validate every resulting architecture with the same complete `ARCH_VALIDATE` implementation. Production-only orchestration SHALL add checkpoints and repair-budget decisions around those shared functions but SHALL NOT introduce a looser validator, alternate prompt renderer, full-draft repair path, protocol-specific branch, or calibration-artifact publication path. (Design 7.1.1: §6.4.1, §6.4.3-§6.4.4, §6.4.8; D1.0/D1.11; M1-4c.)

#### Scenario: Production architecture needs repair
- **WHEN** the initial candidate fails one or more repairable ARCH_VALIDATE gates and the architecture repair budget remains
- **THEN** the same approved repair template and patch machinery receive the current candidate, normalized failures, and allowed paths, after which the full Schema and ARCH_VALIDATE suite reruns

#### Scenario: A production-only validator would accept more
- **WHEN** an architecture fails any gate accepted by neither the shared calibration nor production validator
- **THEN** production S4 rejects it instead of bypassing or weakening that gate
