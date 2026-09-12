## Purpose

Define a reproducible and auditable M1-13 study that measures naturally occurring F0/F1 failure causes and supplies evidence-bound production-parameter rationale without enabling F2/F3 revisions.

## ADDED Requirements

### Requirement: The study admits only preregistered real-run evidence
The M1-13 study SHALL publish its configuration, sample-unit definition, inclusion and exclusion rules, configuration-equivalence rule, intended run set, and stopping rule before admitting results. An admitted run SHALL be a finalized real execution using frozen inputs and a frozen requested provider/configuration route with `budgets.revision_f2_limit=0` and `budgets.revision_f3_limit=0`; its evidence SHALL pass the existing applicable Run, Plan, State, ledger, build, smoke, and hash checks. A third-party service's returned model identity SHALL be recorded exactly when supplied and MAY be absent, aliased, or vary across calls without changing configuration equivalence or invalidating an otherwise admissible run. A transient disconnect, timeout, rate limit, server error, or other recoverable provider interruption SHALL keep the logical run pending and resumable under the existing bounded budget and evidence rules rather than becoming a study failure sample; completed and interrupted call evidence and consumed usage SHALL be retained. Calibration trials, synthetic or injected failures, unfinalized runs, finalized `internal_error` runs, and runs outside the preregistered set SHALL NOT enter the natural-failure denominator. If a run actually finalizes as `internal_error`, the formal configuration group SHALL remain recorded as invalid and SHALL be rerun as a whole rather than repaired by dropping or replacing only the bad run. (Design: system design §9.2, §10.2.2-§10.2.3, §10.8; pipeline §13.4; M1-13.)

#### Scenario: A valid real run is admitted
- **WHEN** a preregistered finalized real run has frozen F2/F3 limits `0/0`, belongs to the declared configuration group, and all required artifact and hash checks pass
- **THEN** the study admits the run and records its run id, configuration identity, observed model identities, and immutable evidence references

#### Scenario: Third-party model identity is unstable
- **WHEN** the frozen requested provider route and call configuration are unchanged but returned model names are absent, use aliases, or differ between calls
- **THEN** the study discloses the observed identity values or absence and does not reject the run or split the configuration group for that reason

#### Scenario: A provider call is transiently interrupted
- **WHEN** a preregistered logical run encounters a recoverable disconnect, timeout, rate limit, or endpoint error before finalization
- **THEN** execution may resume the same run under existing budgets, retains the interruption and usage evidence, and does not count the interruption as a natural failure sample

#### Scenario: A synthetic failure is encountered
- **WHEN** a fixture, fault injection, calibration trial, or synthetic trigger demonstrates an F0/F1 or F2/F3 mechanism
- **THEN** the study excludes it from the natural-failure denominator and reports it only in the separate synthetic-evidence table

#### Scenario: One formal run has an internal error
- **WHEN** any run in a formal configuration group terminates with `internal_error`
- **THEN** the study marks that complete group invalid, retains it for audit, and admits no selectively retained member or replacement run from that group

### Requirement: Real-call progress monitoring is read-only and non-authoritative
Long-running real LLM execution SHALL use a separate low-cost monitoring agent when that agent capability is available. The monitor SHALL only observe the primary agent's reported execution state and existing run/status artifacts, stay quiet while state is unchanged, and report meaningful progress, persistent provider interruption, completion, failure, or required user action. It SHALL NOT edit files, run provider experiments independently, change or retry a run, diagnose root causes, develop or review code, classify samples, select parameters, alter the preregistration, or produce an owner decision. The primary implementation agent SHALL retain every analytical, implementation, experimental-control, and acceptance decision and SHALL verify monitor reports against authoritative artifacts before acting. (Operational constraint: user direction for M1-13; Design: monitoring decision; M1-13.)

#### Scenario: A long provider run remains active
- **WHEN** the primary agent starts the preregistered real-call batch and a low-cost monitoring agent is available
- **THEN** the monitor observes progress without mutations and reports only meaningful state changes while the primary agent remains responsible for the run

#### Scenario: The monitor suggests a technical conclusion
- **WHEN** the monitoring agent proposes a code change, root-cause classification, parameter value, retry action, or acceptance decision
- **THEN** that suggestion has no authority, causes no mutation, and is independently handled or rejected by the primary agent from the authoritative evidence

### Requirement: Every natural failure sample is uniquely identified and evidence bound
The study unit SHALL be one terminally unsuccessful F0/F1 task in an admitted run, identified by the run id and stable task uid and evaluated over its complete accepted attempt and lease history. Each sample SHALL bind the frozen configuration, initial and active Plan references, terminal Plan State, applicable state history and revision ledger, every relevant attempt and diagnostic, build and smoke evidence, workspace commit/tree, and the exact evidence used for the classification. Multiple attempts for the same task SHALL NOT become multiple root-cause samples, and a task from an excluded run SHALL NOT be admitted. (Design: system design §5.2.4, §5.4, §6.6, §9.2, D1.14; pipeline §5.6-§6.1; M1-13.)

#### Scenario: A failed task has several F0/F1 attempts
- **WHEN** one admitted task uid has multiple failed attempts or an F1 lease before reaching its terminal unsuccessful state
- **THEN** the manifest contains one sample row for that task with references to the complete relevant history

#### Scenario: Required evidence is missing or inconsistent
- **WHEN** a candidate sample lacks a required artifact, has a broken hash/reference, or cannot be reconciled to its admitted run and task uid
- **THEN** the audit rejects the sample from the classified denominator and reports the exact evidence defect without guessing a root cause

### Requirement: Root-cause judgments distinguish planning and local implementation defects
Each admitted failure sample SHALL record a reasoned judgment of `planning_defect` or `local_implementation_defect` based on its bound evidence. F0/F1 exhaustion alone SHALL NOT prove a planning defect, and a Diagnoser hypothesis or model self-report SHALL be supporting evidence rather than classification authority. If the available evidence cannot support either judgment, the sample SHALL be marked `indeterminate`, excluded from the two-category proportion denominator, and counted as an evidence limitation; the study SHALL NOT force a classification to improve coverage. (Design: system design §6.6.1, §10.2.2, D1.14; pipeline §6.1, §13.4; M1-13.)

#### Scenario: A local repair remained possible under the accepted plan
- **WHEN** the complete evidence shows the accepted Plan, contracts, ownership, and task granularity remained valid and the failure arose in code within the applicable F0/F1 repair boundary
- **THEN** the sample is classified `local_implementation_defect` with the decisive evidence and rationale recorded

#### Scenario: The accepted plan could not express the required repair
- **WHEN** the complete evidence deterministically shows that satisfying the frozen obligation required an F2/F3-class ownership, task-granularity, dependency, interface, module, or layout change outside the applicable F0/F1 boundary
- **THEN** the sample is classified `planning_defect` with the decisive evidence and corresponding applicable trigger facts recorded

#### Scenario: Evidence supports neither category
- **WHEN** the retained evidence is valid but insufficient to distinguish a planning defect from a local implementation defect
- **THEN** the sample is recorded as `indeterminate`, remains visible in coverage counts, and is excluded from the two-category ratio

### Requirement: Study results are deterministically recomputable and separate evidence classes
The study SHALL deterministically recompute the admitted run count, natural failure-task count, classified and indeterminate counts, planning-defect and local-implementation-defect proportions, classification coverage, observed TR predicate facts, and applicable existing M1 metrics from the frozen manifest and referenced artifacts. Results SHALL list every contributing run id and sample id, SHALL report real and synthetic evidence in separate tables, and SHALL reject duplicate sample identities, cross-configuration aggregation not declared by the protocol, denominator substitution, and manual totals that disagree with recomputation. Small samples SHALL be reported as counts and descriptive ranges without p-values or significance claims. (Design: system design §9.1.4, §9.2, §10.8, D1.14; pipeline §9, §13.4; M1-13.)

#### Scenario: The same manifest is recomputed twice
- **WHEN** the unchanged protocol, manifest, classifications, and referenced artifacts are audited twice
- **THEN** both runs produce byte-equivalent machine results and the same report tables and denominators

#### Scenario: A duplicate or mixed sample is supplied
- **WHEN** the manifest repeats a run/task identity, silently mixes undeclared configuration groups, or places synthetic evidence in the real sample set
- **THEN** recomputation fails with a specific audit error and publishes no accepted aggregate

### Requirement: Parameter rationale remains evidence bound and non-enabling
The M1-13 output SHALL give a selected value, a retain-current recommendation, or an explicit `insufficient_evidence` result for `revision.theta2`, `revision.theta6`, `budgets.revision_f2_limit`, `budgets.revision_f3_limit`, `budgets.s6_lease_limit`, `revision.rho_min_f2`, `revision.rho_min_f3`, `budgets.s6_total_attempts_cap`, `smoke.dwell_seconds`, and `smoke.term_grace_seconds`. Every result SHALL cite admitted real evidence and explain how it follows from the root-cause distribution or why the evidence cannot support a change. Apart from the separately authorized `architecture_planner.max_tokens=65536` correction, the study SHALL NOT modify production configuration or use the design's example starting values as frozen proof. In the absence of sufficient natural evidence, F2/F3 production limits SHALL remain `0/0` and the report SHALL state that D1.14 and production enablement are not established. (Design: system design §4.7, §8.3, §10.2.2-§10.2.3, PQ-1, D1.14; pipeline §6.1, §11 PQ-1, §13.4; M1-13.)

#### Scenario: Evidence supports a parameter recommendation
- **WHEN** the admitted real samples provide a traceable basis for one of the listed parameters
- **THEN** the output records the proposed value or retain-current decision with the exact supporting sample and aggregate references, without changing production configuration

#### Scenario: Natural failures are absent or insufficient
- **WHEN** there are no admitted natural failures or the sample/classification coverage is inadequate to justify one or more parameters
- **THEN** each unsupported parameter is marked `insufficient_evidence`, production F2/F3 limits remain `0/0`, and the study does not claim D1.14 or production readiness

### Requirement: Production ArchitecturePlanner receives a larger bounded completion budget
Production configuration SHALL set a role-level `max_tokens` override of `65536` for `architecture_planner`. The shared T1 `max_tokens` value SHALL remain `16000`, so `task_planner`, `spec_extractor`, `spec_merger`, and other T1 consumers do not inherit the larger budget. The provider request SHALL continue to use the existing single `max_tokens` field; this change SHALL NOT claim to distinguish hidden reasoning tokens from visible response tokens or add provider-specific reasoning parameters. A real experiment using the new value SHALL be preregistered as a fresh configuration group with new config and snapshot hashes, and SHALL NOT pool the prior 16K outcomes into its denominator. (Design: system design §8.3 role overrides and §9.2 configuration equivalence; M1-13 owner correction.)

#### Scenario: ArchitecturePlanner route resolves the larger budget
- **WHEN** the production configuration resolves the `architecture_planner` route without a tier override
- **THEN** its `max_tokens` is `65536` while the T1 tier remains `16000`

#### Scenario: Another T1 role resolves its route
- **WHEN** a T1 role without its own token override is resolved
- **THEN** its `max_tokens` remains the T1 value `16000`

#### Scenario: A new real experiment is prepared
- **WHEN** M1-13 runs after the production ArchitecturePlanner budget changes
- **THEN** the study publishes a new preregistration and frozen configuration identity before Provider I/O and analyzes the new group separately from every 16K group

### Requirement: Responsible-owner acceptance is an explicit final gate
The study SHALL NOT be marked complete or archived until a responsible owner explicitly reviews and signs the frozen protocol, admitted and excluded sample inventories, every classification and limitation, deterministic results, report, and parameter rationale. Automated validation SHALL record technical consistency but SHALL NOT create, infer, or substitute for the owner decision. (Design: system design §10.2.2, D1.14; OpenSpec project rules; M1-13.)

#### Scenario: Machine checks pass without an owner decision
- **WHEN** all focused audit and OpenSpec checks pass but no responsible-owner signature exists
- **THEN** the change remains incomplete and no D1.14 acceptance is claimed

#### Scenario: The owner approves the frozen evidence package
- **WHEN** the responsible owner records a dated decision naming the reviewed protocol, evidence package, report, parameter rationale, limitations, and requested corrections if any
- **THEN** the owner gate is satisfied only for that exact reviewed package, subject to post-decision focused revalidation after any correction
