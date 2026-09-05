# plan-revision-infrastructure Specification

## Purpose

Define the deterministic and crash-recoverable infrastructure that classifies preserved work, advances immutable Plan versions, and binds Plan State, file ownership, revision history, the active pointer, and Run metadata into one auditable chain.

## Requirements

### Requirement: Migration classification is complete and deterministic
Given an old active Plan, candidate Plan, old Plan State, file ledger, and optional explicit split/merge lineage, the system SHALL emit one canonical migration report accounting for every old and new task and every old `realized` file. It SHALL use only task uid, obligation/guidance digests, exact responsibility/file sets, contract export signatures, status and explicit lineage. It SHALL classify unchanged obligation as `INHERIT`; owner-only reassignment of unchanged realized content as `REVALIDATE`; a changed obligation on a previously done task with no new file and responsibility Jaccard at least 0.5 as `AMEND`; and a new task, never-done task, task with a new deliverable file, or task below that Jaccard threshold as `REGENERATE`. It SHALL NOT infer lineage from names, text similarity or file overlap. (Design 7.2.0: §5.2.2-§5.2.5; pipeline design 1.3.0 §3.1-§3.2; M1-4d.)

#### Scenario: Only guidance changes
- **WHEN** matched tasks have equal uid and obligation digest but different guidance digest
- **THEN** the task and its surviving realized files are classified `INHERIT`

#### Scenario: A task is split explicitly
- **WHEN** candidate tasks have new uids and the supplied lineage records their `derived_from` old uid
- **THEN** classification uses that declared lineage and records the old/new mapping

#### Scenario: A task resembles an old task without lineage
- **WHEN** a new uid has overlapping files or similar text but no explicit lineage
- **THEN** it is `REGENERATE` and no inferred ancestry is recorded

#### Scenario: Classification inputs are replayed
- **WHEN** byte-identical inputs are classified twice
- **THEN** the migration reports are byte-identical and contain canonically ordered task/file rows

### Requirement: Preservation rate is a file-level pre-activation fact
The migration report SHALL compute `preservation_rate` as the count of old realized files classified `INHERIT` or `REVALIDATE` divided by the total old realized-file count. Removed realized files SHALL remain in the denominator and be classified `REGENERATE`; new files SHALL not enter the old denominator. A zero denominator SHALL yield exactly `1.0`. Aggregate counts SHALL be recomputable from the report rows and SHALL NOT replace them. (Design 7.2.0: §9.1.4; pipeline design 1.3.0 §3.3-§3.4; M1-4d.)

#### Scenario: No realized file exists
- **WHEN** the old file ledger contains only slot-only rows
- **THEN** preservation rate is `1.0` and every aggregate count is recomputable

#### Scenario: A realized file loses its slot
- **WHEN** an old realized path is absent from the candidate Blueprint
- **THEN** it remains in the denominator as `REGENERATE` and is retained for later quarantine handling

### Requirement: File and revision ledgers are closed append-only contracts
The file ledger SHALL use top-level `files` and closed conditional rows for `slot_only`, `realized`, and `quarantined` evidence. The revision ledger SHALL use `entries` with consecutive `revision_seq` beginning at 1; the first `prev_entry_sha256` SHALL be 64 zeroes and every later value SHALL equal the previous complete entry's canonical SHA-256. Each Plan revision entry SHALL bind from/to Plan refs, level, trigger and patch metadata supplied by the future revision controller, complete task/file migration rows and counts, preservation rate, gate evidence, epoch, activation workspace commit and cost. Validation SHALL reject aliases, missing rows, count drift, a broken hash chain, duplicate sequence, or mutation of a prior entry. (Design 7.2.0: §4.3, §5.2; pipeline design 1.3.0 §3.3-§3.4, §4.2-§4.3, §6.4; M1-4d.)

#### Scenario: First revision is appended
- **WHEN** an empty valid ledger receives revision sequence 1
- **THEN** its previous-entry hash is exactly 64 zeroes and the complete canonical entry validates

#### Scenario: A prior entry is changed
- **WHEN** any byte of an earlier entry changes while a later entry retains the former predecessor hash
- **THEN** chain validation fails and no active pointer is trusted

#### Scenario: Migration summary drifts
- **WHEN** entry counts or preservation rate do not equal deterministic recomputation from its task/file rows
- **THEN** the entry is rejected as damaged

### Requirement: Plan versions and epochs advance monotonically
Plan versions SHALL use C.A.P with C fixed at 1 for one run. An F2 Plan activation SHALL increment P by one and preserve epoch; an F3 Plan activation SHALL increment A by one, reset P to zero, and increment epoch from `E<n>` to `E<n+1>`. Revision sequence SHALL increment by exactly one independently of version components. Every version path SHALL be `plan/versions/plan-<C.A.P>.json`, and once activated its canonical bytes SHALL never change. F1 ledger entries, trigger evaluation, and F2/F3 candidate generation SHALL NOT be implemented by this capability. (Design 7.2.0: §4.3-§4.4; pipeline design 1.3.0 §4.1-§4.4; M1-4d/M1-4e boundary.)

#### Scenario: F2 is activated
- **WHEN** active version is 1.2.3 in E2 and an approved F2 candidate is supplied
- **THEN** the only legal target is 1.2.4 with E2 and the next revision sequence

#### Scenario: F3 is activated
- **WHEN** active version is 1.2.3 in E2 and an approved F3 candidate is supplied
- **THEN** the only legal target is 1.3.0 with E3 and the next revision sequence

#### Scenario: A version skips or regresses
- **WHEN** candidate version, sequence or epoch differs from the unique legal successor
- **THEN** activation fails before a version file or mutable control artifact is changed

### Requirement: Revision activation has one locked logical commit point
Activation SHALL require a validated candidate Plan, migration report, projected Plan State/file ledger, append candidate and expected current pointer under the existing controller lock. Before changing a live artifact it SHALL persist a canonical `_s4r/rev_NNN/activation.json` WAL binding old/new refs and hashes. It SHALL then publish the immutable Plan version, atomically replace migrated State and file ledger, atomically replace the append-only revision ledger, atomically advance `active_plan.json`, and finally update only the Run's active-pointer reference. Pointer advancement SHALL be the logical commit point; the immutable S4 `output_refs.plan` SHALL remain bound to 1.0.0. A caller SHALL NOT activate an unvalidated partial migration or bypass the lock. (Design 7.2.0: §4.8, §5.2, §6.4.7; pipeline design 1.3.0 §4.2-§4.4, §6.4; M1-4d.)

#### Scenario: Activation completes
- **WHEN** every input and expected old hash validates and every write succeeds
- **THEN** Plan, State, both ledgers, active pointer and Run reference form one self-consistent new active revision

#### Scenario: Concurrent controller attempts activation
- **WHEN** another cooperating controller holds the run lock
- **THEN** activation changes no artifact and reports the existing lock failure

#### Scenario: An input changes after rehearsal
- **WHEN** current pointer, State, file ledger, revision ledger or candidate hash differs from the WAL-bound expectation
- **THEN** activation fails closed rather than recomputing or accepting the drift

### Requirement: Crash recovery converges to one legal revision state
Recovery SHALL use the active pointer as authority and the immutable activation WAL as reconciliation evidence. If the revision ledger did not advance, recovery SHALL restore old State/file-ledger bytes and isolate an unactivated candidate version under its `_s4r/rev_NNN/` directory. If the ledger advanced but the pointer did not, recovery SHALL verify and complete the new State/file ledger and then advance the pointer. If the pointer advanced, recovery SHALL only verify all new artifacts and complete a missing Run active-pointer reference. Conflicting bytes, an absent required WAL, invalid chain, or non-monotonic version/epoch SHALL be reported as artifact damage without guessing, deleting realized content, or rolling back an activated pointer. (Design 7.2.0: §4.8, §5.2; pipeline design 1.3.0 §4.2-§4.4, §6.4; D1.13; M1-4d.)

#### Scenario: Crash occurs before ledger append
- **WHEN** version or mutable migration outputs exist but the ledger remains at the old sequence
- **THEN** recovery restores the WAL-bound old mutable bytes, isolates the candidate version, and leaves the old pointer active

#### Scenario: Crash occurs after ledger append
- **WHEN** the new entry and referenced artifacts validate but the pointer remains old
- **THEN** recovery advances the pointer and then completes the Run reference

#### Scenario: Crash occurs after pointer advancement
- **WHEN** all revision artifacts agree but Run still references the prior pointer bytes
- **THEN** recovery updates only the Run active-pointer reference

#### Scenario: Recovery sees conflicting bytes
- **WHEN** any live or WAL-bound artifact has an unexpected hash
- **THEN** recovery reports damage and performs no speculative repair
