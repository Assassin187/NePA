# ArchitecturePlanner V2 optimizer decision

## Decision

- Result: **blocked; do not admit V2 and do not modify the shared prompt**.
- No calibration, Provider, or API call was started.
- Evidence lineage: `a85f28d7f96f41cc7ef615c4bd03e95cbd8d1ed134f7d60894ecb4ce7ffbc415` only. V0 and V1 are complete and source-match-valid.
- Configured role assignment is authoritative for this analysis. Returned model labels are not used as a blocker or as evidence attribution.
- Current parent prompt SHA-256: `0f074a933a8f90e54e98cd348acfa01121fe17dece3766f430714c56da8f3271`.
- Resulting prompt SHA-256: `0f074a933a8f90e54e98cd348acfa01121fe17dece3766f430714c56da8f3271` (byte-identical; no edit).
- V1 remains a failed screening result. Its fallback position, if any is later computed after complete V0～V4 evidence, would not be a quality-gate pass.

## Single candidate prompt hypothesis considered but not admitted

The one prompt-impactable candidate was contract-backed test-readiness closure: the prompt does not give an explicit algorithm for making every `gate=task` test's responsible work packages converge while keeping each `depends_on` list exactly equal to the providers of consumed task-ready contracts. An explicit projection could target the coupled `arch_10`/`arch_08` oscillation without restating V1's concrete-file-ledger hypothesis.

Supporting V1 samples are:

- Initial `ARCH_TEST_READINESS_UNCLOSED` occurs in qwen trials 001/002, claude trials 001/002, and deepseek trial 001, with 43 initial issues across those five trials. The V1 aggregate raw frequency remains high at qwen 28, claude 30, and deepseek 18 across repair depths.
- Claude trial 002 P1 closes `arch_10` by replacing `wp-entry-main.depends_on` with eighteen free dependencies, but regresses `arch_08` with `ARCH_DEPENDENCY_MISMATCH`. P2 restores the exact contract-derived dependency to only `wp-transport-io`, closes `arch_08`, and restores all eleven readiness failures.
- DeepSeek trial 001 has the same oscillation: P1 adds dependency/contract projections, closes `arch_10`, and regresses `arch_08`; P2 removes the mismatched dependencies, closes `arch_08`, and restores nine readiness failures.
- Claude trial 001 P1 demonstrates that the unchanged validator can accept a candidate that closes `arch_10` without an `arch_08` regression. This makes the closure pattern plausibly prompt-influenceable rather than an `arch_08` or `arch_10` validator rejection of every legal solution.

If it were admissible, this one hypothesis would target:

- `arch_10` / `ARCH_TEST_READINESS_UNCLOSED` as the primary target;
- `arch_08` / `ARCH_DEPENDENCY_MISMATCH` as the same-invariant regression guard.

It is not admitted because the same complete V1 evidence exposes a deterministic validator/protocol blocker that directly controls repeated final failures and prevents a clean prompt-only V2 interpretation.

## Blocking validator and repair-locality defect

`arch_15` fails initially in all 9/9 V1 trials and remains failed in 5/9 final candidates. Part of this cluster is ordinary model prose outside the whitelist, but a repeated residual is mechanically valid according to the frozen Schema and design and mechanically impossible according to the frozen validator:

1. ArchitectureDraft Schema and the authoritative design permit `path_pattern` to contain exactly `{type_id}` when `expand_over="types"`; the current prompt repeats that legal rule.
2. Frozen `nepa/speclib/architecture.py` tokenizes `src/types/{type_id}.c` into `src`, `types`, `type`, `id`, and `c`.
3. The validator's allowed set contains the unsplit string `type_id`, but not the emitted token `type`. It therefore returns `ARCH_PATH_TOKEN_INVALID` for a Schema-legal required placeholder form.
4. Claude V1 trial 003 P1 is otherwise clean and retains `src/types/{type_id}.c`; P1 and P2 each contain exactly one issue: `arch_15 / ARCH_PATH_TOKEN_INVALID`, token `type`, at the type-source layout slot. The identical residual appears in Claude V0 trial 001 P1/P2, so it is not caused by the V0→V1 prompt edit.
5. The locality contract compounds path-token repairs. DeepSeek V1 trial 003 P2 changes `apps/broker_main.c` to the whitelist-safe `apps/entry.c`, closes `arch_15`, but the allowed paths do not permit the dependent module/work-package file projections to be updated. Full revalidation correctly reports regressions in `arch_02`, `arch_09`, and `arch_12`; locality therefore fails.

A prompt workaround would have to suppress a Schema/design-legal `{type_id}` layout or teach models to avoid path changes whose required closure fields are excluded from the repair path set. That would tune output around a validator/locality inconsistency and would not be a valid prompt-only optimization. Changing the validator, failure-to-path mapping, Schema, or protocol would change lineage-controlled components and requires a fresh lineage; this optimizer is not authorized to make those changes.

## Cause classification

- **Prompt:** The contract-backed readiness candidate is supported by repeated `arch_10`/`arch_08` content and could be revisited after the blocker is removed. No prompt edit is admitted now.
- **Validator:** Blocking. `{type_id}` is accepted by Schema/design but deterministically rejected after incompatible tokenization. Frozen validator bundle SHA-256 is `f2c2e0b43e20eb48bff2567a872b4ff664c78cfa03ced2cb1fec00c484263b1f`; bundled `nepa/speclib/architecture.py` SHA-256 is `fdf950c9170489ce1322dbb0e087cc57ce15cec56e63138ccea2a69f202f759b`.
- **Experiment protocol/locality:** Blocking for some `arch_15` path repairs. The allowed-path projection can expose the layout path without the ownership/file-partition projections that must change with it, making a local gate fix regress full validation.
- **Runner/infrastructure:** Not causal. V1 assessment status is complete, all three slots have `infrastructure_invalid=false`, zero truncations, complete usage/trace refs, and source-match-valid prompt bytes. Patch applications and full revalidation follow the candidate changes rather than failing nondeterministically.
- **Returned model label:** Not causal and not a validity check here; configured logical slots are used as explicitly directed.

## Prompt modification and complete unified diff

No prompt bytes changed. The complete unified diff is empty:

```diff
```

No validator, Schema, runner, protocol, configuration, test, design document, old artifact, lineage leaf, or prompt-development version artifact was modified.

## Regression risk avoided

Admitting a readiness edit now could make `arch_10` look better while the known false `arch_15` rejection still controls P2, confounding attribution. A path-token wording workaround could also bias models away from legal per-type layouts, increase static-file concentration, or encourage non-local path substitutions that regress `arch_02`, `arch_06`, `arch_09`, or `arch_12`.

## Required next validation after parent-side correction

Do not run V2 in this lineage. The parent must first resolve the validator/design tokenization conflict and the coupled repair-path closure, then create a fresh lineage because validator and locality mapping are lineage-bound.

Before any new Provider I/O, deterministic evidence should prove:

- both legal placeholders `{message_id}` and `{type_id}` pass `arch_15` token derivation;
- Spec-derived identifiers containing separators are judged under one documented, design-consistent token rule;
- an `arch_15` path replacement can update every mechanically coupled ownership/file-partition projection within the allowed repair closure, or is rejected before the call as non-repairable;
- all fifteen gates and historical negative path-neutrality cases still behave as specified.

Only after a fresh, complete, infrastructure-valid three-slot baseline repeats the readiness defect should the single contract-backed readiness hypothesis be reconsidered. Its falsification metric should be declared against that new baseline: reduce P0 `ARCH_TEST_READINESS_UNCLOSED` incidence in at least two slots without increasing P0 `ARCH_DEPENDENCY_MISMATCH`, and then apply the unchanged quality criterion of at least 2/3 P2 successes in every slot. Fallback superiority must not be described as screening success.

## Evidence references

Canonical V1 aggregates:

- `prompt-development/versions/v1/assessment-n003.json` — `a12f256f135ca2e2069bba1fff38bb023d7e368f30d91b737ea9875f5758f1c5`
- `v1/qwen/calibration_report.json` — `9759a62f892fd228a1f1c4ec74f8e55fe5ed36baa38381ada112fa623cfce2d9`
- `v1/claude/calibration_report.json` — `a21d0d2439e608b2213c0a1cd1905fbb7ba024188064ea9d338cb7f0d3b23934`
- `v1/deepseek/calibration_report.json` — `601f37466196500e2d9b0f3688ceaff022c8854696fd72e2602c4042334b7fe8`

Raw blocker evidence:

- `v1/claude/candidates/trial_003_p1.json` — `802aec41ab378585da124812b4ca3923c959f76d8fc63218e57d786e0b2ff901`
- `v1/claude/validations/trial_003_p1.json` — `26da237d156cc6b04d1dae87ee1a42099e2888637e4a3dcd9ec738c9c9545f1b`
- `v1/claude/validations/trial_003_p2.json` — `af74fd8dd07501990871fbb7fff1c574993cf3af7875e52972f1c0d77b154782`
- `v1/claude/locality/trial_003_p2.json` — `3771c431b5719f3f6dee57f9ecdca7943eede0440b80878f308f5183a083700f`
- `v1/deepseek/patches/trial_003_p2.json` — `189d343c98d642f9ae0901d7ef8791ece248f18d657243c66abf4e5539ee85a2`
- `v1/deepseek/validations/trial_003_p2.json` — `219d31ea92e479287c294077874b7cc266928a3949354398fd050105ef36cde8`
- `v1/deepseek/locality/trial_003_p2.json` — `4d7c4ccc0f4bff669c9c396fc9dd60194a931d4c2acdfe0ff4196c0d09413c92`

Raw readiness diagnostic evidence:

- `v1/claude/validations/trial_002_p0.json` — `3ef16def482c883c52035b4ea211cbf5f8db78abe88afd46713da2cdea24c269`
- `v1/claude/validations/trial_002_p1.json` — `da25f726a7607e9501ae9d83bec12d6112abe7c2ec0c7007bf4c7fe6701ec26d`
- `v1/claude/validations/trial_002_p2.json` — `b461d60c3c8ba55e7ecd964c7c21e03bd3371be852b45747627f126f7b941d09`
- `v1/claude/locality/trial_002_p1.json` — `3ae183ce27810b7b8459176b0368f78877c55a089bb941ff4a561c90e6cd0c32`
- `v1/claude/locality/trial_002_p2.json` — `1517577470afa723868d21388780a60ef0cb8877ff8f02ed01da0bc1d84ab803`
- `v1/deepseek/validations/trial_001_p0.json` — `8a0c8d73c65e4ad759d92a398787d6e2b9d745192b5612503fdf0bdf71ba7bc0`
- `v1/deepseek/validations/trial_001_p1.json` — `a035e17b904b396b959c75e3f1cbd97a181935f4502f0f23a6339c7878551f9b`
- `v1/deepseek/validations/trial_001_p2.json` — `3c7a3c05bf40036444f4653638a1b22f726e69bccf07dd679b9dd80525cccca2`
- `v1/deepseek/locality/trial_001_p1.json` — `405eae16479c5e15d7deefa8155f37ff3a855c922f2d513f422405b116e4e70f`
- `v1/deepseek/locality/trial_001_p2.json` — `aa4c1661b6955fb5884650ebfa99ca1afd0dba8ce3b131b932fbdb94070b6a45`

V0 comparison:

- `v0/claude/validations/trial_001_p1.json` — `a8dbfe05dc5f9801dc487bcc0ea273c8ab09976eb3af881da420e03591e4d69a`
- `v0/claude/validations/trial_001_p2.json` — `0b98a35134685bf5e2f20125af84bcdab1b8f2fd0c21c77c37a84f5f858d3fab`

Frozen control refs:

- `lineage.json` — `49b9ad8abb983dd33ea71c45accd2d296fa30f134ccce44dae7cf682f24b676d`
- `prompt-development/protocol.json` — `7eb71dd2bcb39fee2b9077bdbdb3ea2505d74113783a5a687ef18505ae89ef3d`
- `components/validator.bundle.json` — `f2c2e0b43e20eb48bff2567a872b4ff664c78cfa03ced2cb1fec00c484263b1f`
- `schema/architecture-draft.schema.json` — `53c936e83f7bf4cf53011438b4df153334ac58aa22ad484859bd0c6a651e43d6`

## Remaining budget

- V0 + V1 consumed 6 initial trials per model.
- 9 initial trials per model remain.
- One revision (V1) has been admitted; at most three revision slots remain (V2～V4).
- This blocked optimizer decision admits no revision and consumes no initial, semantic-repair, format-repair, Provider, or API call.
