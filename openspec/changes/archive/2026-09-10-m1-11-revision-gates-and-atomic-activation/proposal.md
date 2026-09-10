## Why

M1-10 now turns accepted S6 boundary facts into typed trigger events and complete, immutable, non-authoritative F2/F3 candidates, but NePA still pauses at `revision_handoff`: no controller evaluates RG-1 through RG-5, records a rejected candidate, or commits a candidate as the next active Plan. M1-11 is the next serial work item and must close that mechanism boundary so the remaining gate/activation portion of D1.13 can be verified before M1-12 adds post-activation evaluation, circuit breaking, or degradation.

## What Changes

- Add one deterministic revision-gate controller that revalidates the selected trigger and candidate, evaluates RG-1 through RG-5 in order, persists every gate result, and stops at the first failure.
- Reuse the existing PlanCritic contract for RG-4 with only the candidate delta closure; M1-11 acceptance uses a stub Agent or frozen response and does not add or calibrate PlanReviser.
- Add F3 rehearsal in a temporary copy of the accepted workspace using the existing S5 rendering/materialization path; require structural closure and idempotence, and allow build failures only when they are wholly attributable to the candidate's registered affected groups. F2 records RG-5 as `not_applicable` without materialization.
- Append exactly one idempotent `candidate_rejected` event when any gate fails, while leaving the active pointer, formal version chain, Plan State, file ledger, Run active reference, binding, and workspace unchanged.
- Add the locked F2/F3 activation transaction: project migrated State and file ledger, publish the immutable successor Plan and F2 metadata binding or F3 pending-materialization intent, append `revision_activated`, and make atomic `active_plan.json` advancement the sole logical commit point.
- Reconcile every activation interruption before stage admission. If the pointer is still old, restore the WAL-bound old State/file ledger/revision ledger/current copies and isolate unactivated Plan/binding bytes even when an activation entry was prewritten; if the pointer is new, validate all new bytes and only complete missing Run/current-copy publication before continuing.
- Consume the existing S6 `revision_handoff`: F2 resumes S6 through the already delivered REVALIDATE/AMEND/REGENERATE paths, while F3 makes the next S5 epoch pending and enters the delivered multi-epoch materialization and affected-group path.
- Extend the frozen internal configuration contract with `budgets.revision_f2_limit`, `budgets.revision_f3_limit`, `revision.rho_min_f2`, `revision.rho_min_f3`, and `revision.cost_rates.build_usd`; enabled mechanism fixtures provide explicit trial values, while uncalibrated production remains disabled at 0/0.
- Deliver protocol-neutral positive/negative fixtures, every individual gate rejection, F2/F3 success, replay, corruption, and every activation crash window under the existing `revision_mechanism` marker.
- **Out of scope:** M1-12 `revision_evaluated`, effectiveness/circuit-breaker/level-closing/degradation behavior; M1-13 parameter selection; M1-14 PlanReviser role, prompt, Agent call, calibration, or production enablement; M1-15 production runs; TR-9 execution; S7/S8/M2 assets; public CLI switches; edits to `project_docs/`, prompts, calibration lineage, or archived changes.

## Capabilities

### New Capabilities

- `revision-gates-and-activation`: Ordered RG-1 through RG-5 evaluation, rejection publication, F3 rehearsal, successful F2/F3 activation, handoff routing, and mechanism-level acceptance behavior.

### Modified Capabilities

- `plan-revision-infrastructure`: Make pointer advancement the sole activation commit point, define complete F2/F3 binding/publication state, and replace the stale ledger-before-pointer recovery wording with the authoritative pointer-old rollback rule.
- `agent-invocation-runtime`: Permit the existing PlanCritic role to receive the compact delta closure for RG-4 without gaining patch, execution, activation, or PlanReviser authority.
- `s6-f0-execution`: Replace the former closed F2/F3 boundary with consumption of an accepted M1-10 revision handoff and deterministic continuation after M1-11 rejection or activation.

## Impact

- **Milestone/work item:** M1-11 only. The prerequisite is the archived and owner-approved `m1-10-revision-triggers-and-patch-operators` change, whose final record reports 730 passing tests and strict OpenSpec acceptance. Governing authority is `project_docs/system_design.md` §4.7, §5.2.4, §5.6.7, §6.4.6, §8.3, §10.2.1-§10.2.2, §10.8 and D1.13, plus `project_docs/pipeline_design_s4_s9.md` §2-§6.5 and §7.1-§7.3.
- **Affected existing paths:** revision candidate and ledger helpers in `nepa/speclib/revision_mechanism.py` and `nepa/speclib/plan_revision.py`; S6/orchestrator handoff and pre-admission reconciliation; existing S5 multi-epoch rehearsal/materialization helpers; RunStore atomic artifact publication; Agent role binding; configuration and revision/activation evidence Schemas.
- **Internal interfaces/artifacts:** gate-result and rehearsal evidence, complete activation WAL, typed `candidate_rejected`/`revision_activated` payloads, F2 binding receipt, migrated State/file ledger, and S5/S6 continuation signals. No public command or CLI argument changes.
- **Upstream/downstream:** M1-10 candidate bytes and trigger ancestry remain immutable inputs; M1-8 and M1-9 remain the only S5/S6 consumers for post-activation materialization and migration execution. M1-12 receives accepted activations and execution results later but is not implemented here.
- **Acceptance:** `uv run pytest -q -m revision_mechanism` must cover every gate and all §5.6.7 activation windows, followed by the §10.8 public CI and strict OpenSpec validation. The result establishes the remaining M1-11 mechanism portion of D1.13 only; responsible-owner review of the final implementation and acceptance package is a separate required gate and cannot be inferred from machine checks.
