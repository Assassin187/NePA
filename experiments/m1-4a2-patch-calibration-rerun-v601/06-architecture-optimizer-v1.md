# ArchitecturePlanner V1 optimizer result

## Decision

- Result: recommend Luna adopt this prompt as the sole V1 candidate for the next preregistered three-slot run.
- This is a prompt-revision recommendation, not a quality-gate pass. No calibration or provider/API call was started.
- Evidence lineage: `a85f28d7f96f41cc7ef615c4bd03e95cbd8d1ed134f7d60894ecb4ce7ffbc415` only.
- Evidence version: complete V0 N=3 for qwen, claude, and deepseek, including every trial's request/response references, candidates, patches, applications, locality results, validations, traces, usage, model strings, slot reports, and `assessment-n003.json`.
- Parent prompt SHA-256: `4d97877fb99a1382018bfc933e7452877e838d5ed11cbd82acf08ef679fbfdb5`
- Proposed prompt SHA-256: `0f074a933a8f90e54e98cd348acfa01121fe17dece3766f430714c56da8f3271`

## Single falsifiable hypothesis

H1 — The shared prompt's former one-sentence pattern-expansion instruction is too implicit. Models are treating `layout.files` declarations as if they were the concrete S6 ownership ledger, so they copy `s5_frozen` paths and/or unexpanded `path_pattern` literals into `modules[].owns_files` and `work_packages[].allowed_files`. Requiring one explicit concrete S6 ledger, then deriving module ownership and work-package partitions from that same ledger, should reduce the coupled `arch_02`/`arch_06`/`arch_09` first-pass failures across providers.

This revision changes only that projection algorithm. It does not add an independent remedy for dependency, requirement-primary, readiness, path-token, or repair-locality failures.

## Supporting failures

At P0, `arch_02`, `arch_06`, and `arch_09` each failed in 8/9 trials. The coupled error-code counts were:

| Slot/trial | Target gates | Target error-code counts |
| --- | --- | --- |
| qwen/001 | arch_02, arch_06, arch_09 | `ARCH_MODULE_FILE_INVALID=5`, `ARCH_WORK_PACKAGE_FILE_PARTITION=5`, `ARCH_DELIVERY_CONSTRAINT_VIOLATION=6` |
| qwen/002 | arch_02, arch_06, arch_09 | `5`, `5`, `6` respectively |
| qwen/003 | arch_02, arch_06, arch_09, arch_12 | `7`, `4`, `8`; `ARCH_LAYOUT_OWNER_CARDINALITY=18` |
| claude/001 | arch_02, arch_06, arch_09, arch_12 | `7`, `5`, `8`; `ARCH_LAYOUT_OWNER_CARDINALITY=18` |
| claude/002 | arch_02, arch_06, arch_09, arch_12 | `7`, `5`, `8`; `ARCH_LAYOUT_OWNER_CARDINALITY=18` |
| claude/003 | arch_02, arch_06, arch_09 | `15`, `5`, `16` respectively |
| deepseek/001 | arch_02, arch_06, arch_09 | `4`, `4`, `5` respectively |
| deepseek/002 | arch_02, arch_06, arch_09 | `1`, `1`, `2` respectively |
| deepseek/003 | none of the target gates | no target error code |

The raw candidates show the same mistake, not merely similar gate labels:

- qwen/001 and qwen/002 put frozen headers and build files such as `include/mqtt/types.h` or `Makefile` in module ownership. qwen/003 additionally copied `src/types/{type_id}.c` and `src/codec/{message_id}.c` literally into module and work-package projections.
- claude/001 and claude/002 combined frozen headers/build files with unexpanded type/message patterns. claude/003 expanded the per-identifier S6 paths but still included frozen headers and a build file in module ownership.
- deepseek/001 and deepseek/002 included frozen headers in module ownership; deepseek/003 used concrete S6 paths and was the only P0 sample without the target gate cluster.

The repair evidence is directionally consistent. qwen/001 P1 removed frozen ownership and closed `arch_02`, `arch_06`, and `arch_09`; claude/003 P1 rebuilt the concrete ledger and passed all fifteen gates; deepseek/003 P2 restored an exact concrete ledger and passed all fifteen gates. These are diagnostic examples only: V0 still has `screening_pass=false`, and its final P2 counts are qwen 0/3, claude 1/3, and deepseek 1/3.

## Target gates and codes

Primary targets:

- `arch_02` / `ARCH_MODULE_FILE_INVALID`
- `arch_06` / `ARCH_WORK_PACKAGE_FILE_PARTITION`
- `arch_09` / `ARCH_DELIVERY_CONSTRAINT_VIOLATION`

Secondary, same-invariant diagnostic:

- `arch_12` / `ARCH_LAYOUT_OWNER_CARDINALITY`

`ARCH_PATH_TOKEN_INVALID`, `ARCH_DEPENDENCY_MISMATCH`, `ARCH_REQUIREMENT_PRIMARY_INVALID`, and `ARCH_TEST_READINESS_UNCLOSED` remain observed V0 failures but are outside H1 and were not edited for this revision.

## Why this is prompt-influenceable, not a validator/runner/protocol defect

- The current lineage is complete; all slots report `infrastructure_invalid=false`, zero truncations, stable model strings, resolvable trace references, and recorded usage. The effective prompt parent hash is consistent across the nine P0 trials.
- The same frozen validator accepts corrected file projections: qwen/001 P1 closes all three target gates, claude/003 P1 passes all gates, and deepseek/003 P2 passes all gates. That behavior contradicts a systematic validator rejection of valid ledgers.
- Patch/application rejection is working as specified. qwen/002 and qwen/003 attempted to relabel frozen layout entries and produced Schema-invalid candidates; deepseek/001 and deepseek/002 replaced required non-empty ownership lists with empty arrays; claude/002 attempted an implicit stable-key array insertion. Preserving the prior candidate in these cases is not evidence that the file invariant is wrong.
- Locality failures correspond to actual new regressions, including `arch_08` or `arch_10`, and the full gate set was revalidated after accepted patches. They must not be counted as successful repairs, but they do not explain the repeated P0 file-ledger error.
- The earlier invalid runtime/replay lineage is retained only as provenance in `04-v0-infrastructure-invalid.json`; the admitted evidence here is the reinitialized lineage recorded by `05-lineage-reinitialization.json`. No old-lineage trial contributes to H1.

Therefore there is no current validator, runner, experimental-protocol, or infrastructure blocker that requires stopping this one prompt revision.

## Exact prompt modification

Only Rule 2.d of the shared ArchitecturePlanner prompt changes. The complete unified diff from the lineage V0 prompt is:

```diff
--- a/nepa/agents/prompts/architecture_planner.md
+++ b/nepa/agents/prompts/architecture_planner.md
@@ -39,7 +39,7 @@
    b. Define modules and contracts with closed ids, responsibilities, non-goals, providers, consumers, and interface files. Keep module and work-package contract projections equal to literal scans of the declarations. Derive task-ready dependencies only from the unique provider work package.
    Before finalizing a contract, choose exactly one legal readiness form: an `s5` contract has literal `owner` and `provider` `s5`, uses only non-empty `s5_frozen` interface slots, and is provided by no module or work package; a `task` contract has a module id as both `owner` and `provider`, uses a non-empty subset of that module's `s6_owned` `owns_files`, and is provided by exactly one work package belonging to that same module. Reflect those declarations exactly in the matching module and work-package contract projections. Never use an `s5_frozen` or generated interface file for a task contract, use a module id as the provider of an `s5` contract, or treat narrative ownership as a substitute for these literal projections.
    c. Declare `layout` with roots, a complete file list, and a three-segment build graph. Each file has a unique `slot_id`, a static `path` or exactly one allowed `path_pattern`, `expand_over`, class, render rule, owner module, contract binding, build role, and general responsibility purpose. Use only `{message_id}` with `messages` or `{type_id}` with `types`; never mix, repeat, or invent placeholders.
-   d. Expand each declared pattern over the corresponding derived identifier set when checking ownership, allowed files, contracts, and graph closure. Every `s6_owned` expanded path belongs to exactly one module and exactly one work-package file partition. No task may claim an `s5_frozen` path.
+   d. Materialize one canonical concrete S6 file ledger from `layout.files` before filling any file projection: ignore every `s5_frozen` entry; copy each `s6_owned` static path literally; expand each `s6_owned` `path_pattern` over the complete corresponding derived identifier set and record every concrete path, never the pattern literal or `slot_id`. Set each module's `owns_files` to exactly the ledger paths whose `owner_module` names that module. For each module, its work packages' `allowed_files` must be non-empty, pairwise disjoint subsets whose union is exactly `module.owns_files`, with no `s5_frozen`, unexpanded, or undeclared path. Use this same concrete ledger when checking task-ready contract `interface_files` and graph closure.
    e. Make the build graph closed: every entry and link-source slot exists, each link-source slot enters exactly one artifact, artifact output paths are unique, the delivery-form entry-point and executable-artifact counts are exact, and the graph is acyclic.
    f. Make contract provider-to-consumer edges agree with the convention layer order and contain no reverse edge or cycle. Keep every path segment and every purpose token within the general responsibility vocabulary or the derived identifier set.
    g. Re-run the complete ordered checks for all fifteen architecture gates before returning the object.
```

## Expected improvement and regression risks

Expected improvement: more P0 candidates should exclude frozen files and unexpanded patterns from task ownership, yielding a single exact module/work-package projection and reducing target-gate repair demand.

Risks to measure rather than mask:

- Enumerating concrete paths may increase output tokens and could raise truncation risk on larger derived identifier sets.
- Exact-equality language may prompt broad replacement patches, increasing patch application rejection or locality regressions.
- Closing the file-ledger cluster may expose or shift failures into contracts, dependencies, readiness, or graph closure (`arch_03`, `arch_05`, `arch_08`, `arch_10`) without improving final P2 success.
- A model may omit a legitimate static S6 path if it misreads `owner_module`; `arch_02`, `arch_06`, `arch_09`, and `arch_12` must therefore all remain enabled and unchanged.

## Next-round validation

Run no experiment as part of this optimizer action. If Luna adopts V1, validate it under the unchanged preregistered N=3 protocol in all three slots and within the existing budget.

Primary falsification metric:

- Baseline is 8/9 P0 trials failing at least one of `arch_02`, `arch_06`, or `arch_09`. H1 is supported only if V1 reduces this to at most 4/9 and the reduction appears in at least two of the three slots. Otherwise reject or revise H1 rather than changing validators.

Secondary diagnostics:

- Report each target gate's P0 pass count by slot and aggregate counts for the three target error codes; retain `arch_12` owner-cardinality incidence as a same-invariant diagnostic.
- Compare ledger-related patch rejections, locality failures, changed-path breadth, and semantic repair depth with V0.
- Compare output tokens, format repairs, truncations, Schema validity, and infrastructure validity with V0; require zero truncations and `infrastructure_invalid=false` for all slots.
- Check that P0 incidence of `arch_03`, `arch_05`, `arch_08`, `arch_10`, and `arch_15` does not materially regress. Do not add a second prompt remedy in the same V1 test.
- Apply the actual quality criterion only at the end: every slot must reach P2 pass rate at least 2/3 with the screening infrastructure conditions satisfied. A fallback or a single winning slot is not a quality-gate pass.
