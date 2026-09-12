## Context

See `proposal.md` for motivation. M1-12 is archived with owner approval, production revision limits are `0/0`, and the existing runtime already persists the Run, Plan, Plan State/history, revision ledger, attempt, diagnostic, build, smoke, trace, and workspace evidence needed for a retrospective study. `nepa.metrics.compute_m1_metrics` provides the M1 metric projection, while the general `nepa eval runs` CLI is intentionally deferred to M2-6. The current `runs/` root contains only `_calibration`, so implementation must generate the preregistered formal runs rather than reinterpret calibration evidence.

M1-13 is primarily a research-evidence workflow. Its normative boundary is system design §9.2, §10.2.2-§10.2.3, §10.8 and D1.14, plus pipeline design §6.1, §9 and §13.4. The work must keep raw evidence in the existing ignored `runs/` root and publish only stable references and derived research artifacts to git. The only runtime configuration correction is the owner-directed role-level `architecture_planner.max_tokens=65536` override; public contracts remain unchanged. The real calls use a third-party Claude-compatible service, so returned model identity and endpoint availability are observationally noisy and must not be promoted into stricter gates than the authoritative design defines.

Implementation entry found that the existing stream adapter rewrote the configured third-party Claude endpoint's returned identity to the requested label and rejected a response with no model field. The responsible owner explicitly authorized the minimum correction on 2026-09-11: keep the public `LLMResponse.model` string contract, retain within-stream consistency checks, and add exact returned-identity-or-absence metadata without changing routing, persisted Schemas, or any other runtime behavior.

## Goals / Non-Goals

**Goals:**

- Preregister the exact evidence boundary before observing formal outcomes.
- Preserve a one-row-per-terminal-failed-task audit trail from classification through aggregate and parameter rationale.
- Recompute every count and proportion from referenced artifacts with a narrow M1-only tool.
- Produce a truthful signed result even when the conclusion is that evidence is insufficient.
- Complete the declared real-call batch despite recoverable third-party interruptions by resuming the same logical runs and treating returned model-name variation as disclosure-only metadata.
- Give only ArchitecturePlanner a bounded `65536` completion budget and run a separately preregistered group under that configuration.

**Non-Goals:**

- Generalize the study tool into `nepa eval runs`, add a public command, or change existing M1 metric formulas.
- Make semantic root-cause classification an LLM decision or an automatic trigger.
- Change configuration defaults other than the explicit ArchitecturePlanner role override, add separate reasoning/body budget parameters, enable revisions, calibrate PlanReviser, or perform M1-14/M1-15.
- Rewrite historical runs, copy raw run directories into git, or modify design documents and archived changes.

## Decisions

### 1. Use one tracked study root and leave raw runs in place

Implementation will create:

```text
experiments/m1-13-natural-failure-root-cause-study/
├── 00-implementation-brief.md
├── 01-preregistration.md
├── 02-sample-manifest.json
├── 03-root-cause-results.json
├── 04-root-cause-report.md
├── 05-parameter-recommendation.json
├── 06-owner-decision.md
└── scripts/audit_root_causes.py
```

The manifest uses workspace-relative Run paths plus SHA-256 references to required artifacts; it does not copy ignored evidence. Results and the report are regenerated from the manifest and classifications. The owner decision names hashes of the reviewed tracked package so later corrections cannot inherit approval silently.

Alternative considered: commit complete run directories. Rejected because `runs/` is the established raw-evidence boundary and contains large provider/workspace artifacts. Alternative considered: store only a narrative report. Rejected because D1.14 requires per-sample traceability and recomputation.

### 2. Preregister configuration groups and sample selection before runs

`01-preregistration.md` fixes the formal run ids/prefixes, input hashes, resolved configuration snapshot identity, sandbox image digest, requested provider route/model slot and request parameters, F2/F3=`0/0`, independent-cache rule, group size/stopping rule, and inclusion/exclusion rules. Each formal configuration group follows system design §9.2: all runs are retained; a run that actually finalizes as `internal_error` invalidates the complete group and a replacement is a fresh whole-group attempt. Provider-returned model identities are raw observations only: missing identities, aliases, spelling/version changes, or multiple observed values are disclosed with call counts but do not invalidate or split a group while the requested route and configuration remain frozen.

Transient provider failures before finalization are operational interruptions, not experimental outcomes. The primary agent reopens or resumes the same logical Run through the existing persisted resume path, keeping the same preregistered identity and retaining all completed/failed call records and charged usage. Timeouts, disconnects, rate limits, and third-party 5xx responses do not trigger sample exclusion or a fresh group merely because they occurred. Existing retry, total-call, time, and cost limits remain authoritative; this change does not add an unbounded retry loop or modify runtime error semantics. If recovery eventually produces a finalized admissible run, it is analyzed normally; if the run actually finalizes `internal_error`, only then does the §9.2 whole-group invalidation rule apply. A prolonged external outage pauses progress and is reported as requiring later resume, not misclassified as a planning or implementation defect.

This change uses the default N=5 for one formal configuration group; it does not take the design-permitted cost-sensitive N=3 reduction. No sampling continues merely to obtain a desired number or category of failures. Zero natural failures is a valid study observation and leads to the insufficiency path.

Alternative considered: collect failures opportunistically from unrelated runs. Rejected because it permits cherry-picking and leaves configuration equivalence undefined. Alternative considered: require the provider-returned model string to equal the requested Claude label. Rejected because the third-party service may return unstable aliases and system design §9.2 explicitly makes returned model identity record-only. Alternative considered: restart the full group after every transient endpoint failure. Rejected because an unfinalized interruption can be resumed without changing the logical sample; the whole-group rule applies only after a finalized `internal_error`.

### 3. Treat one terminal failed task as one sample

The stable identity is `(run_id, task_uid)`. The row points to the initial and terminal Plan/State context and the complete relevant ordinary-attempt/F1 history; repeated attempts do not inflate the denominator. The audit script confirms the task is terminally unsuccessful in an admitted finalized real run and that every required reference belongs to the same run and hashes correctly.

Classification is a recorded research judgment, not a deterministic inference. The analyst chooses `planning_defect`, `local_implementation_defect`, or `indeterminate`, writes a bounded rationale, and cites decisive evidence. A planning-defect judgment must identify the concrete F2/F3-class change required outside F0/F1 and the applicable machine trigger facts; exhaustion or Diagnoser prose alone is insufficient. `indeterminate` remains visible but is excluded from the two-category proportion, and classification coverage is always reported.

Alternative considered: count each attempt or Diagnoser output as a sample. Rejected because D1.14 concerns failed tasks and repeated repair attempts are correlated evidence for one task. Alternative considered: force every task into two categories. Rejected because that would manufacture certainty when evidence is incomplete.

### 4. Add one narrow deterministic audit script, not a new product surface

The only executable addition is `scripts/audit_root_causes.py`, invoked as:

```text
uv run python experiments/m1-13-natural-failure-root-cause-study/scripts/audit_root_causes.py \
  --study-root experiments/m1-13-natural-failure-root-cause-study \
  --runs-root runs \
  --check
```

It loads the preregistration and manifest, confines every path below the workspace/run roots, verifies JSON/reference/hash/config/sample invariants, assembles the existing input package for `compute_m1_metrics`, and recomputes `03-root-cause-results.json`, the generated tables in `04-root-cause-report.md`, and consistency of `05-parameter-recommendation.json`. `--check` compares regenerated bytes with tracked outputs and makes no writes; a separate explicit generation invocation may publish outputs during implementation. Stable canonical JSON and fixed Markdown ordering make two generations byte-equivalent.

The script validates human classifications and citations but never invents or changes them. No Agent role is called. If a required metric cannot be assembled from existing accepted artifacts, the result reports it unavailable under the existing availability semantics; implementation must not change `nepa/metrics.py` unless a concrete design-blocking gap is reported and separately accepted.

Alternative considered: add `nepa eval runs`. Rejected because system design assigns that public batch CLI to M2-6. Alternative considered: hand-calculate report tables. Rejected because §10.8 requires deterministic recomputation before M2-6.

### 5. Separate real incidence from mechanism evidence

The manifest contains distinct `real_runs`, `natural_failure_samples`, `excluded_runs`, and `synthetic_evidence` collections. Only `natural_failure_samples` contribute to the D1.14 proportions. Synthetic M1-10 through M1-12 fixtures may be cited to show mechanism coverage but always appear in a separate report table with no natural-incidence denominator. Duplicate `(run_id, task_uid)` identities or undeclared cross-group aggregation fail the audit.

Alternative considered: use synthetic fixtures when natural failures are scarce. Rejected explicitly by system design §10.2.2 and pipeline §13.4.

### 6. Make parameter output a recommendation record, not configuration mutation

`05-parameter-recommendation.json` has one row for each PQ-1/M1-13 parameter: `revision.theta2`, `revision.theta6`, `budgets.revision_f2_limit`, `budgets.revision_f3_limit`, `budgets.s6_lease_limit`, `revision.rho_min_f2`, `revision.rho_min_f3`, `budgets.s6_total_attempts_cap`, `smoke.dwell_seconds`, and `smoke.term_grace_seconds`. Each row records `decision` as `select`, `retain_current`, or `insufficient_evidence`, an optional proposed value, rationale, and exact sample/result references.

No code or configuration consumes this record during M1-13. A non-zero revision-limit recommendation is only evidence for later work and cannot bypass M1-14 calibration or M1-15 acceptance. Any unsupported value is `insufficient_evidence`; F2/F3 stay `0/0`, and D1.14/production enablement remain unclaimed.

Alternative considered: update `configs/default.yaml` after the report. Rejected because M1-14 owns the enablement configuration and PlanReviser gate.

### 7. Keep completion behind the responsible-owner gate

Focused tests and `--check` establish structural and arithmetic correctness only. `06-owner-decision.md` remains explicitly pending until a responsible owner reviews hashes for the protocol, manifests, classifications, results, report, recommendation, and limitations. Corrections after review invalidate the named package and require focused regeneration/revalidation plus a new or superseding owner decision.

The change is complete only when the focused technical checks pass and the real owner decision is recorded. If evidence is insufficient, the owner may accept the study's truthful insufficiency conclusion for M1-13 archive, but the record must explicitly state that D1.14 and production enablement were not satisfied.

### 8. Delegate only passive monitoring to a low-cost subagent

When the primary `gpt-5.6-sol` implementation agent starts the long N=5 real-call window, it spawns one `gpt-5.6-luna` subagent if that model is available. The monitor receives a narrow instruction to observe progress only: read current execution messages or status artifacts, remain quiet while state is unchanged, and notify on a completed run, persistent provider interruption, terminal failure, batch completion, or required user action. Monitoring should use bounded waits rather than busy polling.

The monitor has no file ownership and must not mutate the repository or Run artifacts, launch/retry/resume experiments, inspect secrets, diagnose causes, write code, review correctness, classify samples, recommend parameters, or claim acceptance. All such work remains with the primary agent, which independently verifies any notification against Run artifacts before acting. The monitor's messages are operational convenience and never scientific evidence. If `gpt-5.6-luna` is unavailable or the subagent ends, the primary agent continues with the same plan; availability of a monitor is not an experiment or acceptance precondition.

Alternative considered: delegate individual real runs or root-cause analysis to subagents. Rejected because it would split experiment control and high-judgment work across agents. Alternative considered: have the primary agent poll continuously. Rejected because passive low-cost monitoring is sufficient while expensive provider calls are in flight.

### 9. Override only the ArchitecturePlanner completion budget and isolate the new experiment

Set `roles.architecture_planner.max_tokens` to `65536` in both the checked-in default configuration and the Python fallback defaults. Do not change `tiers.T1.max_tokens`; route resolution already supports a role-level override, so this is the narrowest end-to-end configuration path and prevents other T1 roles from receiving the larger budget. The provider adapter continues sending one OpenAI-compatible `max_tokens` value. This correction therefore raises the combined completion allowance available to reasoning and visible JSON but does not introduce or promise separate provider-side budgets.

The completed 16K groups and their report remain immutable historical evidence. Before any new Provider call, create a distinct study configuration and preregistration whose hashes include the `65536` role override, allocate new logical run identities, and retain F2/F3=`0/0`. The new N=5 group is evaluated on its own; the audit must not pool historical 16K results with it. The final M1-13 report and owner decision are regenerated only after the new group is frozen.

Alternative considered: increase `tiers.T1.max_tokens` to `65536`. Rejected because it would also expand unrelated planning and extraction roles. Alternative considered: rewrite the frozen 16K preregistration. Rejected because it would destroy the temporal integrity of completed experimental evidence. Alternative considered: add separate reasoning-token fields now. Rejected because the current third-party OpenAI-compatible contract exposes only one completion limit and the user requested the minimal usable configuration change.

## Risks / Trade-offs

- **[Natural failures may be zero or too few]** → Stop at the preregistered boundary, report counts and insufficiency, keep F2/F3 at `0/0`, and do not add samples after seeing the outcome.
- **[Semantic classification may be subjective]** → Require concrete evidence citations, an explicit F0/F1-versus-F2/F3 boundary rationale, indeterminate handling, and owner review; do not treat Diagnoser output as authority.
- **[Ignored raw evidence can drift or disappear]** → Bind every consumed artifact by relative path and SHA-256 and make the focused audit fail on missing or changed evidence before accepting aggregates.
- **[Task-level samples within a run are correlated]** → Report both run and task counts, avoid p-values/significance claims, and do not present task proportions as independent-run statistics.
- **[Third-party endpoint identity or availability is unstable]** → Freeze requested configuration, record returned identities without equality gates, retain interruption/usage evidence, and resume the same logical run; only a finalized `internal_error` invokes the authoritative whole-group rule.
- **[A monitoring subagent exceeds its role]** → Give it no file ownership or mutation/retry authority, treat its output as non-evidence, and require the primary agent to verify every actionable notification.
- **[Focused validation can miss unrelated regressions]** → Honor the user-directed scope: test the new audit path and any actually modified shared metric module only, while preserving runtime/public files unchanged by diff inspection.
- **[The larger budget can exceed the production context reserve]** → Keep the existing 128K ArchitecturePlanner preflight and 15% margin; the frozen new group must pass preflight before Provider I/O, otherwise pause rather than lowering evidence checks or silently changing the input.

## Migration Plan

1. Verify the archived M1-12 owner record, active-change isolation, default `0/0` limits, current focused metric baseline, and absence of admissible formal runs; then publish the required implementation brief.
2. Add the tracked study skeleton, audit script, and dedicated focused tests; validate using synthetic temporary fixtures only.
3. Preserve the completed 16K evidence, set only the production `architecture_planner` role override to `65536`, and verify route resolution without changing T1.
4. Publish a new configuration file and preregistration with new hashes and run identities before provider execution; start one read-only `gpt-5.6-luna` monitoring subagent when available, complete the new logical runs through bounded resume across transient third-party interruptions, and retain any group that truly finalizes invalid without selective replacement.
5. Freeze the new manifest/classifications, generate and check results/report/recommendations, and run only the M1-13 focused verification commands.
6. Obtain the responsible-owner decision, apply only requested in-scope corrections, rerun the same focused checks, and archive only when the owner gate accurately matches the final package.

Rollback of the configuration correction removes the `architecture_planner.max_tokens` role override from `configs/default.yaml` and the matching Python fallback, restoring inheritance from T1. Raw historical and new Run evidence is retained for audit rather than deleted.
