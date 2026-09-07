## Purpose

Define reusable deterministic calculations for M1 code-generation, lineage, build, smoke, revision and F1 lease metrics so later reports and evaluation commands consume one formula source rather than reinterpreting execution artifacts.

## ADDED Requirements

### Requirement: M1 metrics are pure projections of accepted evidence
The production adapter SHALL follow the active and initial Plan references, the accepted S6 receipt and its sequential State-history snapshot, receipt-bound build/smoke results, ledger evidence references and immutable attempt records. It SHALL associate telemetry by stable `output_path`, and SHALL NOT infer accepted results from directory ordering or synthetic call identifiers.

The metric capability SHALL calculate results only from validated current/historical Plans and State, explicit activation lineage, immutable task/joint evidence, accepted stage receipts, accepted revision/lease events and their associated telemetry. Identical canonical inputs SHALL produce identical outputs without mutating a run, and Agent-reported completion or expected effect SHALL not contribute to measured results. Missing or invalid required artifacts SHALL yield an unavailable value with a machine-readable reason; a valid empty ledger SHALL yield zero event counts. (Design: §5.4, §5.6.7, §9.1.4; pipeline §9; M1-7.)

#### Scenario: Inputs are replayed
- **WHEN** the same validated metric input package is evaluated twice
- **THEN** both result objects are byte-equivalent and no source file or run state changes

#### Scenario: Ledger is legally empty
- **WHEN** the initial Plan seal is valid and `revision_ledger.entries` is empty
- **THEN** revision and lease counts are zero and active-plan-dependent calculations anchor to 1.0.0/E0/0 without reading a nonexistent activation

#### Scenario: Required evidence is unavailable
- **WHEN** a metric needs a missing receipt, invalid ledger or unverifiable evidence reference
- **THEN** that result is null with an explicit reason rather than inferred from directory order, process memory or Agent text

### Requirement: Task-state rates preserve final and initial-plan semantics
The calculator SHALL produce `task_completion_rate@final`, `blocked_rate@final` and `incomplete_rate@final` from equal-weight current task rows, and SHALL produce their `@r0` counterparts by following explicit obligation lineage from each equal-weight initial task. An initial task SHALL count complete only when all of its frozen obligations have current valid completion proof; otherwise a blocked required successor makes it blocked and every other unfinished case makes it incomplete. It SHALL compute `ever_blocked_rate` over the union of all activated historical uids that ever had a blocked status. Every complete/blocked/incomplete triplet SHALL sum to one, and an empty task set SHALL return null with `EMPTY_TASK_SET`. (Design: §9.1.4; fixed metric cases; M1-7.)

#### Scenario: Initial task is split
- **WHEN** initial A splits to A1/A2, B remains done, A1 is done and A2 is blocked
- **THEN** final completion is 2/3, `@r0` completion is 1/2, final blocked is 1/3, `@r0` blocked is 1/2 and only historically blocked A2 contributes to the historical numerator

#### Scenario: Regenerated successor later succeeds
- **WHEN** A2's new generation obtains valid completion proof for the remaining original obligation
- **THEN** both completion rates become one and current blocked becomes zero while historical blocking remains recorded

#### Scenario: Initial tasks merge
- **WHEN** initial A and B merge into C and C has one valid proof covering every predecessor obligation
- **THEN** final completion and `@r0` completion are both one without weighting either predecessor by file count

### Requirement: First-pass rates use immutable execution history
The calculator SHALL define `first_pass_rate` over uids with at least one coding call and count a uid only when its first ordinary attempt in its creation version passed and committed. It SHALL exclude unexecuted and pure-revalidation uids and SHALL not turn an initial failure, AMEND success or later REGENERATE success into an original first pass. It SHALL separately calculate `first_pass_rate_after_revision` over REGENERATE generations whose first new ordinary attempt succeeds. (Design: §9.1.4; fixed metric cases; D1.2; M1-7.)

#### Scenario: Mixed attempt histories are measured
- **WHEN** A succeeds on its first ordinary attempt, B on its second, and C succeeds on the first attempt of a regenerated generation after its original generation failed
- **THEN** `first_pass_rate` is 1/3 and `first_pass_rate_after_revision` is 1/1

#### Scenario: Revalidation has no coding call
- **WHEN** a merged task becomes done solely through a valid revalidation proof
- **THEN** it is excluded from both first-pass denominators

### Requirement: M1 build and smoke results use accepted stage receipts
The calculator SHALL derive `s6_build_ok` only from all default build variants named by the accepted S6 receipt and SHALL derive `smoke.pass` separately for S5 and S6 from every default variant's executable artifacts, including artifacts checked and failure reasons. It SHALL not set S5 smoke true for `pending_repair`. `build_ok` SHALL come only from an accepted S7/S8 terminal full round; for an M1 `planned_stop` without such a round it SHALL be null with `PLANNED_STOP_NO_TERMINAL_ROUND`. (Design: §5.4, §9.1.2, §9.1.4; M1-7.)

#### Scenario: M1 planned stop has successful exit checks
- **WHEN** an accepted S6 receipt proves both default builds and all S6 smoke checks pass but no S7 terminal round exists
- **THEN** `s6_build_ok` and S6 `smoke.pass` are true while `build_ok` is null with `PLANNED_STOP_NO_TERMINAL_ROUND`

#### Scenario: Receipt is absent
- **WHEN** no accepted receipt supplies the required S6 build or smoke references
- **THEN** the corresponding M1 metric is unavailable rather than inferred from the latest build directory

### Requirement: Revision and lease metrics count only their typed accepted events
The calculator SHALL count only `revision_activated` events for `revision.count_by_level`, only `candidate_rejected` events for `revision.rejected_by_gate`, and at most one occurrence of each trigger code per `trigger_evaluated` event for `revision.trigger_histogram`. It SHALL compute migration mix per task, preservation sequence/mean/min per activation, estimated and actual rework costs from their specified event/call associations, effectiveness from terminal resolved evaluations and all related actual model costs, and ineffective count from unique terminal evaluations. It SHALL count `lease_started` for `lease.count`, derive successful and pending outcomes from matching `lease_finished`, and calculate external-file p50/p95 from start-event path counts. F1 events SHALL never count as a Plan activation. (Design: §9.1.4; pipeline §9; M1-7.)

#### Scenario: Lease and revision events coexist
- **WHEN** accepted history has two lease starts with one successful finish and one failed finish, one F2 activation, one RG-2 rejection and one TR-5 observation
- **THEN** lease count is two, success rate is 0.5, pending count is zero, F2 activation count is one, RG-2 rejection count is one and TR-5 histogram count is one

#### Scenario: Lease remains unfinished
- **WHEN** a lease start has no matching accepted finish event
- **THEN** it contributes to lease count and pending count but not the success numerator

#### Scenario: No lease exists
- **WHEN** the accepted ledger contains no lease start
- **THEN** lease count and pending count are zero while success rate and external-file percentiles are null with `NO_LEASES`

#### Scenario: No revision activation exists
- **WHEN** the ledger is empty or contains only F1 events
- **THEN** activation counts are zero, preservation sequence is empty and its mean/min are null with `NO_REVISIONS`

### Requirement: Preservation and rework formulas keep distinct denominators
For each activation, preservation rate SHALL equal old realized files classified INHERIT or REVALIDATE divided by all old realized files, with an empty old-file domain equal to one; new files SHALL not enter that denominator. Actual rework cost SHALL deduplicate model calls associated through activation, group or verification identities and include AMEND/REGENERATE coding and diagnosis but not candidate proposal or critic costs. Estimated and actual costs SHALL remain separate, and zero total effectiveness cost SHALL produce null with `ZERO_COST_DENOMINATOR`. (Design: §9.1.4; pipeline §3.4, §9; fixed metric cases; M1-7.)

#### Scenario: File preservation differs from new-work cost
- **WHEN** ten old realized files classify as six INHERIT, two REVALIDATE, one AMEND and one retired, while two new files belong to one new task
- **THEN** preservation is 0.8 and estimated rework budgets one task execution rather than one execution per new file

#### Scenario: Revision costs are measured
- **WHEN** two revisions resolve one trigger issue with two dollars of total associated model cost, 1.2 dollars of actual rework and three dollars estimated rework
- **THEN** effectiveness is 0.5 problems per dollar, actual rework is 1.2 and estimated rework remains three
