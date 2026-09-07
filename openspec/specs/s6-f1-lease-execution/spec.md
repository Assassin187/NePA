# s6-f1-lease-execution Specification

## Purpose

Define the bounded F1 repair-lease boundary that lets one current S6 Fixer update a narrowly authorized completed neighbor while preserving ownership, joint validation, atomic evidence publication and deterministic recovery.

## Requirements

### Requirement: F1 lease authorization is deterministic and bounded
S6 SHALL authorize an F1 lease only for the current task's already-available Fixer attempt when every leased path is an `s6_owned` file of another currently done task, the lending task is in the same work package or is the current task's direct contract provider, at most two external files are named, the run has remaining `budgets.s6_lease_limit` and global execution capacity, and the authorization evidence identifies the exact current Plan, baseline commit, task uids and paths. Authorization SHALL NOT change a task owner, Plan, epoch or revision sequence, and SHALL NOT implement the later TR trigger evaluator. (Design: §4.7, §5.2.4, §6.6.1, §10.2.2 M1-7; pipeline §7.2.)

#### Scenario: Same-work-package lease is eligible
- **WHEN** a current in-progress task has a remaining Fixer attempt and names one file owned by a done task in the same work package under an unspent lease budget
- **THEN** the controller may authorize exactly that task/path pair for the current attempt without changing either owner

#### Scenario: Direct provider lease is eligible
- **WHEN** the lending done task is the current task's direct contract provider and every other authorization condition holds
- **THEN** the controller may authorize its listed `s6_owned` paths even when the tasks are in different work packages

#### Scenario: Lease scope is ineligible
- **WHEN** a path is frozen, unknown, owned by a non-done task, outside the same-package/direct-provider neighborhood, or would make the external-file count exceed two
- **THEN** S6 rejects the lease before provider I/O and changes no lease budget, State, owner or workspace fact

#### Scenario: Lease allowance is exhausted
- **WHEN** accepted `lease_started` events already equal the frozen run limit or the current/global Fixer allowance is unavailable
- **THEN** S6 starts no lease and does not manufacture another ordinary attempt

### Requirement: A lease consumes one existing Fixer attempt before invocation
Before provider I/O, S6 SHALL atomically persist the current ordinary Fixer attempt and run-wide call usage as defined by F0, append one idempotent `lease_started` event, consume one lease allowance and bind its `lease_id`, baseline commit, current execution count, lending uid/paths and authorization evidence. The Fixer SHALL receive the ordinary required context plus only the authorized neighbor-file bytes and lease scope; the lending task's ordinary attempts and State SHALL remain unchanged. A lease SHALL NOT add a Coder/Fixer call beyond the current task's four-attempt limit. (Design: §5.2.4, §5.6.7, §6.6.2; pipeline §6.5, §7.2; D1.6.)

#### Scenario: Lease call begins
- **WHEN** an eligible lease is granted for the current task's next normal Fixer attempt
- **THEN** the attempt, global usage, evidence sequence and `lease_started` event are durable before the one Fixer invocation begins

#### Scenario: Invocation is interrupted
- **WHEN** execution stops after `lease_started` but before the Fixer result is accepted
- **THEN** resume preserves both the spent ordinary attempt and spent lease allowance and does not append another start event for the same lease

#### Scenario: Neighbor history remains private
- **WHEN** the leased Fixer context is assembled
- **THEN** it contains the authorized neighbor files and lease evidence but no unrelated task history, files, Spec slices or test implementation

### Requirement: Successful F1 execution is jointly validated and committed
An F1 candidate SHALL change only the current task's owned files and the exact leased paths, SHALL preserve all sealed export declarations, and SHALL pass the required builds and executable smoke checks for both participating tasks on one candidate tree; M1 applicable tests remain empty. S6 SHALL publish a new Task Evidence object for every member with `execution_kind=lease`, one sorted Joint Evidence object and exactly one joint commit carrying only `NePA-Verification-ID` and `NePA-Joint-Evidence-SHA256` evidence trailers. The current task SHALL become done, the lending task SHALL remain done, both acceptance proofs and realized file rows SHALL bind the same tree/commit, and owner history SHALL not change. (Design: §5.2.4, §5.4, §5.6.7, §6.6.1; pipeline §7.2; M1-7.)

#### Scenario: Two-member lease succeeds
- **WHEN** an authorized candidate changes one current file and one leased file, preserves declarations, and every joint build and smoke gate passes
- **THEN** S6 publishes two task evidence objects, one joint evidence object, one joint commit and one all-member State/file-ledger result bound to the same tree

#### Scenario: Only one member contributes changed files
- **WHEN** a valid current-only or leased-only non-empty subset repairs the shared candidate tree and all members pass joint validation
- **THEN** every member receives joint Task Evidence while File Ledger changes are limited to the files that actually changed

#### Scenario: Candidate exceeds the granted paths
- **WHEN** the Fixer response names an otherwise related neighbor file that was not listed by the accepted lease
- **THEN** S6 rejects the candidate before live workspace mutation and records no successful joint verification

#### Scenario: Export declaration changes
- **WHEN** any current or leased candidate file changes a sealed exported declaration or its implementation binding
- **THEN** the F1 candidate fails before commit even if its build would otherwise pass

### Requirement: F1 publication and recovery are all-member atomic
S6 SHALL use `plan/verification_pending.json` to bind the lease, baseline/candidate trees, every member's evidence sequence and reference, joint evidence, expected commit trailers, old/new State, old/new file ledger and old/new revision ledger. The legal joint commit SHALL be the transaction commit point. Before it, failure or recovery SHALL restore the common baseline, leave the lender done, preserve the current task's consumed attempt and failure artifacts, and append exactly one failed `lease_finished`; after it, recovery SHALL verify and complete all members, one `verification_committed` event and one successful `lease_finished` without another Agent call, validation run or commit. Conflicting or partial accepted facts SHALL fail closed. (Design: §5.4, §5.6.7; pipeline §7.2; M1-7.)

#### Scenario: Joint validation fails
- **WHEN** either member's required build or smoke check fails before a legal joint commit
- **THEN** S6 restores the common baseline, leaves the lending task's prior done proof valid, retains the current attempt artifacts and records one failed lease finish with its reason and call references

#### Scenario: Crash occurs before joint commit
- **WHEN** some member or joint evidence exists but no trailer-valid joint commit exists
- **THEN** reconciliation accepts neither member as newly verified and returns the workspace to the WAL-bound baseline

#### Scenario: Crash occurs after joint commit
- **WHEN** the legal joint commit and all immutable evidence exist but State, file ledger or lease/verification events are only partly published
- **THEN** reconciliation completes every missing all-member output and event exactly once without replaying the Fixer or validation

#### Scenario: Only one member appears accepted
- **WHEN** State, ledger or evidence claims the joint result for a strict subset of the participating task uids
- **THEN** execution lint and reconciliation report artifact damage instead of accepting or rolling back one member independently
