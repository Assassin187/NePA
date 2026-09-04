## 1. Design and Change Reconciliation

- [x] 1.1 Update `project_docs/system_design.md` to design 7.0.0 and reconcile proposal, specs, design and tasks with single-model M1-4a2, removal of M1-4a2r/M1-4a3 and direct owner-approved M1-4c handoff. (Requirements: all. Verify: `openspec validate m1-4a2-patch-calibration-rerun --strict` and design reference scan.)

## 2. Single-Model Development Protocol

- [x] 2.1 Replace active fixed three-slot protocol assumptions with exactly one configuration-selected safe `model_slot`; set defaults to one Claude-backed slot while keeping model/provider names out of code and Schema gates. Preserve legacy readers for historical evidence. (Requirements: Development uses one configured model and at most three versions; Configured single-model trial execution is isolated and cache-independent.)
- [x] 2.2 Change the active version state machine to V0～V2, N=3 and nine initial generations; permit revisions to change initial, repair or both templates and record both diffs. (Requirements: Development uses one configured model and at most three versions; Prompt revisions may update either or both stages.)
- [x] 2.3 Make Schema/semantic/truncation/infrastructure outcomes trial-local, retain at most two additional attempts for the same infrastructure-failed trial, and remove whole-version invalidation/retry. (Requirements: Trial failures are isolated; Calibration metrics preserve fixed denominators and failure evidence.)
- [x] 2.4 Select the first version with p2 at least 2/3; after V2 below threshold publish only the deterministic diagnostic reference and no recovery/tie/M1-4c handoff. Require owner approval before an M1-4c-only handoff. (Requirements: Two of three passing trials select the baseline; Terminal failure produces a diagnostic reference only; Owner approval gates M1-4c handoff.)

## 3. Repair Behavior and Prompts

- [x] 3.1 Normalize repair issue paths to stable identifiers, admit multiple independently allowed paths in one patch, and keep exact layout-reference projection without rejecting unrelated allowed edits. (Requirements: Semantic repair is explicit, fresh, and bounded by the declared protocol.)
- [x] 3.2 Allow one fresh correction after a patch is rejected for format, path or application semantics at each semantic depth; preserve the candidate and consume depth only after successful application. (Requirements: M1-3 provides the required built-in role skeletons; Semantic repair is explicit, fresh, and bounded by the declared protocol.)
- [x] 3.3 Rebuild `architecture_planner_initial.md` and `architecture_planner_repair.md` from the historically successful construction algorithm plus current free-layout rules. Ensure repair is self-contained and both templates remain protocol/model neutral. (Requirements: M1-3 provides the required built-in role skeletons.)

## 4. Contracts, Tests and Experiment Reset

- [x] 4.1 Introduce the active single-slot artifact contract and examples for V0～V2, per-trial attempts, minimum-usability selection, diagnostic-only terminal result and M1-4c handoff; retain legacy contract validation and recomputation. (Requirements: New evidence is isolated from historical protocols; Trial artifacts and calibration reports are hash-bound and recomputable.)
- [x] 4.2 Update deterministic tests for arbitrary single-slot configuration, Claude-backed default, 2/3 selection, 1/3 continuation, V2 failure, trial-local infrastructure handling, dual-template revisions, patch correction, multi-region patches, stable paths, prompt completeness and legacy isolation. (Requirements: all.)
- [x] 4.3 Mark existing experiment lineages as design-7.0.0 diagnostic provenance in version-controlled notes, stop attempt 005, and initialize a fresh no-I/O lineage with zero of nine initial trials after all deterministic checks pass. Do not modify historical run leaves or make provider calls. (Requirements: New evidence is isolated from historical protocols.)

## 5. Validation and Live Boundary

- [x] 5.1 Run focused and full tests, strict OpenSpec validation, design/protocol-neutrality checks, historical recomputation checks and `git diff --check`; inspect the complete diff for unrelated changes. (Requirements: all.)
- [x] 5.2 Complete the explicitly authorized live execution: run V0 and only required V1/V2 provider trials; after the authorized trial-local infrastructure retries, V1 achieved 3/3 at p2, the responsible owner approved V1, and the M1-4c handoff was published. (Requirements: Two of three passing trials select the baseline; Owner approval gates M1-4c handoff.)
