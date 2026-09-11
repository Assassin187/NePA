## Purpose

Define how NePA evaluates an activated plan revision, derives bounded revision closure from accepted evidence, and preserves useful execution work while exiting predictably when no legal repair path remains.

## ADDED Requirements

### Requirement: An activated revision receives one evidence-bound terminal evaluation
The system SHALL evaluate an activated F2/F3 revision only after every obligation in its frozen affected set has reached a terminal execution result or a hard budget prevents further validation. It SHALL set `resolved=true, ineffective=false` only when the original trigger predicate no longer holds and every original obligation/lineage anchor has successful accepted validation; it SHALL set `resolved=false, ineffective=true` when the same trigger signature remains after one bounded affected-set traversal. When budget or validation evidence is unavailable, it SHALL record or report an unresolved result without claiming either resolution or ineffectiveness. A lower blocked count, task deletion, task renumbering, denominator change, or Agent-reported expected effect SHALL NOT establish resolution. (Design: system §5.6.7, §9.1.4; pipeline §6.1.1, §7.4; M1-12.)

#### Scenario: The original obligations pass and the predicate clears
- **WHEN** every frozen affected obligation has accepted success evidence and recomputation no longer produces the activation's trigger signature
- **THEN** the revision receives one terminal evaluation with `resolved=true` and `ineffective=false`

#### Scenario: The same problem survives a bounded traversal
- **WHEN** the affected set has completed one bounded traversal and recomputation still produces the same trigger signature over the same obligation/lineage anchors
- **THEN** the revision receives one terminal evaluation with `resolved=false` and `ineffective=true`

#### Scenario: Reopened statuses are observed before validation
- **WHEN** activation has only changed affected task statuses to pending or in-progress and the affected set has not completed its bounded traversal
- **THEN** no terminal evaluation is accepted and the status change is not counted as improvement

#### Scenario: A dependency or contract closure obligation remains pending
- **WHEN** the directly changed task is terminal but a successor task included through dependency or contract closure remains pending or in progress
- **THEN** the activation is not evaluated as resolved and execution continues while legal budget remains

#### Scenario: Budget ends validation without proof
- **WHEN** a hard budget prevents the remaining affected obligations from obtaining accepted validation evidence
- **THEN** the result remains unresolved and neither task-count changes nor absent evidence is converted into a resolved or ineffective claim

#### Scenario: A pre-activation call reuses the same task attempt identity
- **WHEN** a migrated execution call and an earlier call share `task_id` and attempt number
- **THEN** only the call bound to the accepted activation is included in evaluation call refs and actual cost

#### Scenario: Zero-cost payload conflicts with telemetry
- **WHEN** an evaluation records zero actual cost but supplied telemetry assigns non-zero cost to one of its associated output refs
- **THEN** deterministic metric recomputation rejects the conflict instead of silently replacing the payload value

### Requirement: Revision availability is a pure ledger projection
The system SHALL derive attempted `(signature, level)` pairs, successful activation counts, consecutive same-level rejection counts, per-level closure and `revision_locked` from the validated revision ledger and frozen configuration. It SHALL NOT persist writable closure or lock flags in Plan, Plan State or Run. Replaying the same accepted ledger and configuration SHALL produce the same projection without side effects. (Design: system §4.7, §5.6.7; pipeline §6.1.1, §7.1, §7.4; M1-12.)

#### Scenario: A signature is rejected at F2
- **WHEN** one accepted candidate rejection binds a trigger signature and level F2
- **THEN** that exact pair is attempted and cannot be proposed again, while an untried applicable F3 route for the signature remains eligible

#### Scenario: F2 activation allowance is exhausted
- **WHEN** accepted F2 activations reach the frozen F2 limit while F3 remains below its independent limit
- **THEN** F2 is closed and a legal F3 problem can still be selected without altering either counter

#### Scenario: Projection is replayed after resume
- **WHEN** resume supplies the same validated ledger and frozen configuration
- **THEN** the derived attempted pairs, level closures and lock result are byte-equivalent and no accepted artifact is rewritten

### Requirement: Rejection streaks close only their own level
Two consecutive accepted gate rejections at the same level for different trigger signatures SHALL close that level. A successful activation at a level SHALL reset only that level's consecutive-rejection streak; events at the other level SHALL neither reset nor consume it. Exhausting or closing one level SHALL NOT close the other level. (Design: system §4.7, §5.6.7; pipeline §7.1, §7.4; M1-12.)

#### Scenario: Two different F2 candidates fail gates consecutively
- **WHEN** two accepted F2 candidate rejections with different trigger signatures occur without an intervening F2 activation
- **THEN** F2 is closed while F3 retains its independently derived availability

#### Scenario: An F2 activation interrupts the F2 rejection streak
- **WHEN** an accepted F2 activation follows one F2 rejection and another F2 rejection occurs later
- **THEN** the later rejection begins a new F2 streak and no F3 streak is changed

#### Scenario: An intervening F3 event occurs
- **WHEN** one F2 rejection, any accepted F3 rejection or activation, and a second different-signature F2 rejection occur in that order
- **THEN** the two F2 rejections remain consecutive for the F2-specific streak and F2 closes

### Requirement: Ineffective revision and unsupported levels lock later revision activity
An accepted ineffective terminal evaluation SHALL lock all later revision proposals for the run. A recorded F4 or F5 diagnosis SHALL likewise close revision activity because those levels have zero in-run allowance. When every F2/F3 route is closed by its independent budget or rejection rules, `revision_locked` SHALL be true. Locking SHALL preserve every accepted Plan version, code commit, evidence object, attempt count and failure fact, and SHALL NOT reopen F0 or fabricate a new candidate. (Design: system §4.7, §5.6.7, §9.1.2; pipeline §7.1, §7.3-§7.4; M1-12.)

#### Scenario: An activated revision is ineffective
- **WHEN** its terminal evaluation records `ineffective=true`
- **THEN** later trigger observations may remain auditable but no new F2/F3 candidate or activation is admitted in that run

#### Scenario: A problem requires F4
- **WHEN** accepted trigger evidence proves the problem exceeds F3 and records an F4 diagnosis
- **THEN** no in-run architecture or commitment change is attempted and the unresolved execution follows the controlled degraded path

#### Scenario: Both supported levels are closed
- **WHEN** F2 and F3 are each closed by their own accepted history and frozen limit
- **THEN** `revision_locked` is true without writing a lock field into Plan, State or Run

### Requirement: Independent executable work survives revision closure
When a repair group, task or affected subgraph cannot continue, the system SHALL block only its proven dependent subgraph and SHALL continue a branch only if it shares no blocking build/runtime dependency and can still run its complete build, smoke and execution acceptance. It SHALL retain valid code from prior accepted commits and all failure evidence. Once no independently verifiable work remains, a static-valid unresolved run SHALL request `EXECUTION_UNRESOLVED`, enter S9, and finish degraded with exit code 10. It SHALL use failed/exit code 20 only when deterministic evidence proves the static contract or required artifact chain invalid. (Design: system §4.7, §6.6, §9.1.2; pipeline §5.6.1, §7.3-§7.4; M1-12.)

#### Scenario: An independent branch remains after a group exhausts
- **WHEN** an affected repair group is exhausted but another pending branch shares no blocking dependency and can pass its complete acceptance gate
- **THEN** the independent branch executes before the run takes its controlled unresolved exit

#### Scenario: A failed group blocks the default build
- **WHEN** an exhausted group leaves the accepted workspace unable to run the complete build required by another branch
- **THEN** that branch is not called independent and the run enters the controlled degraded path without another model call

#### Scenario: Static contract validity is preserved
- **WHEN** revision paths are locked but all authoritative Plan, binding, ledger and invariant checks remain valid
- **THEN** termination is degraded with `EXECUTION_UNRESOLVED`, not failed or internal_error

### Requirement: Hard global exhaustion prevents every further model call
Before each Agent, Coder or Fixer invocation, the system SHALL enforce the frozen global wall-clock/cost budget and, for coding or fixing work, the run-wide S6 attempt cap. Once any applicable hard limit is exhausted, it SHALL persist the existing controlled-exit request before entering S9, SHALL make no further model call, and SHALL run S9 budget synchronization with enforcement disabled so reporting can finish. Resume SHALL honor the persisted usage and request without resetting or refunding a started call. (Design: system §4.7-§4.8; pipeline §6.5, §7.3-§7.4; D1.10; M1-12.)

#### Scenario: Global cost is exhausted before a revision-side call
- **WHEN** synchronized accepted usage has reached the frozen cost limit before a candidate, critic, coding or fixing invocation
- **THEN** the invocation does not start and the run persists a budget controlled-exit request before S9

#### Scenario: The S6 call cap is exhausted
- **WHEN** the run-wide S6 attempt counter equals its frozen cap before another Coder or Fixer allocation
- **THEN** no call is allocated or invoked and static-valid unfinished execution enters the controlled degraded path

#### Scenario: Resume follows a budget exit request
- **WHEN** a run resumes after the controlled-exit request was committed but before S9 finalized
- **THEN** it completes S9 without enforcing the exhausted budget again or starting any model call
