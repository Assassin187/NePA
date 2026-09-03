# M1-4a2 value-hash-free patch calibration implementation brief

Status: restart baseline recorded before Provider I/O for this experiment directory.
Date: 2026-08-29
Change: `m1-4a2-patch-calibration-rerun`

`project_docs/system_design.md` is authoritative. This brief records verified
repository facts for design 6.0.1 and does not modify that document or admit
historical evidence into the new lineage.

## Current design and frozen inputs

| artifact | sha256 |
| --- | --- |
| `project_docs/system_design.md` (design 6.0.1) | `543fe7c37d34ecb4616399b05175e6d6deea34f8057f937a00c87e2aebec8f7d` |
| `project_docs/pipeline_design_s4_s9.md` | `6ebf3c693e519fd14229b3591b4226d51c9df399380f28164d5d981d45c51af8` |
| `gold_file/specIR.json` | `a0ec9616eb06c206416a93220e1ea630d04166eb17e102bc9d9476fe2694aa09` |
| `gold_file/target.json` | `efa8dc8fc0914d5b563a1da1aeaad1a7a277b4b161b89893b8efa75d6818b49b` |
| `gold_file/test_bundle.json` | `8f77eb4c5a15ef0ee02979240fbcd4eebdf585d5ad023d130de9468e791d343c` |
| `configs/m1-4a2-live.yaml` | `74699b7ce39ffd80f5872214e9acaf025f53b03125ca0e081d919b06fc9535a2` |
| `configs/m1-4a2-context-limits.json` | `44ac70a01c185392466512c3783cfd7c2bfb3965a8b83484b50c30ef88e1093e` |

The frozen calibration control is three logical slots (`qwen`, `claude`,
`deepseek`) with `max_tokens=65536`. Current implementation and contract
anchors are:

| component | sha256 |
| --- | --- |
| `nepa/agents/base.py` | `2083d35ad416c4df8057aa4ac11c0c735e20b56bf35e9624fb6875d3f7619990` |
| `nepa/agents/roles.py` | `eaeff1e2c2e83f8ac327d8172a14c20f89affffb75688cf8bc8a809788d7a556` |
| `nepa/agents/prompts/architecture_planner.md` | `4d97877fb99a1382018bfc933e7452877e838d5ed11cbd82acf08ef679fbfdb5` |
| `nepa/calibration/s4_architecture.py` | `82e97e636c466959a627270e251d694e984312c9c5e6877079b870259b898e43` |
| `nepa/calibration/s4_prompt_development.py` | `ab0ace9605ae45905331695705c9873ceb45dbf6df217fccd04ae06f500e36c3` |
| `nepa/speclib/architecture.py` | `fdf950c9170489ce1322dbb0e087cc57ce15cec56e63138ccea2a69f202f759b` |
| `nepa/speclib/planning.py` | `4e265385c7c7d26272d126829e21897dc96b716aa7f65dafe44698842ad6b836` |
| `nepa/speclib/delivery.py` | `394e713cf382629c79aa59fba8de52d4d6e02b37fb584569a320e3adc6205539` |
| `nepa/speclib/lint.py` | `87181cfefb9dbc24d3185600bdc854f5caa1ee451e9ad8b1b2104f0b872dfc62` |
| `nepa/schemas/architecture-draft.schema.json` | `53c936e83f7bf4cf53011438b4df153334ac58aa22ad484859bd0c6a651e43d6` |
| `nepa/schemas/architecture-patch.schema.json` | `dcfa37ed73d8db65e8ae4147e4f0ccc083c07e387036c7af833e7603dc8dc456` |
| `nepa/schemas/examples/architecture-patch.example.json` | `85759f019c07edb3a8aa26508ddee738b26089b2857b2cc0187ea2da3cf913fe` |

The new patch contract is value-hash-free: model patches carry only ordered
`add`/`replace`/`remove` operations and `expected_presence`; lineage, artifact
references, immutable evidence and report recomputation continue to use
SHA-256.

## Historical-only provenance

The prior 6.0.0 value-hash patch lineage is:

`runs/_calibration/s4-architecture/2656c887f54b02c3ec8f7f63a0622d2fb0f6bcb29a366fac51735c5be717d7f0`

Its recorded patch schema hash was
`e5b030ded729487efb2df4ba506d9640128da2ddd2bce08571408cafde18468f`, and
its patch example hash was
`0a19d864deacbd9535d44ed0c599163d9cc79358b650865a258b02d7fda3120f`.
That lineage, its trials, prompt versions and reports remain immutable
provenance only. They are not copied, resumed, screened, ranked, selected or
joined with this 6.0.1 experiment.

The existing historical experiment directory
`experiments/m1-4a2-patch-calibration-rerun/` is likewise read-only for this
restart. Its prior brief records the old design 6.0.0 hash; that claim is not
used as the current design authority.

## Boundary and secret handling

Required environment-variable names are `NEPA_QWEN_API_KEY`,
`NEPA_CLAUDE_API_KEY` and `NEPA_DS_API_KEY`. The preflight may record only
whether each is present; secret values are never read into evidence.

No Provider I/O is admitted until the deterministic task-group-5 gate passes.
This experiment directory is the sole destination for the new human-readable
brief, preregistration and result summary; fresh calibration leaves are stored
under a new lineage below `runs/_calibration/s4-architecture/`.

## Pre-live gate and fresh lineage

The no-I/O preflight is `02-preflight.json`: it resolved all three logical
slots, context limits, frozen inputs and prompt neutrality, and recorded only
environment-variable presence. The deterministic gate completed before the
lineage initialization command:

| command | result |
| --- | --- |
| `uv run python -m pytest -q` | `359 passed in 126.78s` |
| `openspec validate --all --strict` | `6 passed, 0 failed` |
| four gold/design lint commands | all valid, zero errors/warnings |
| `git diff --check` | passed |

The fresh initialization command was:

`./run_m1_calibration.sh init --config configs/m1-4a2-live.yaml --context-limits configs/m1-4a2-context-limits.json --runs-root runs --spec gold_file/specIR.json --target gold_file/target.json --test-bundle gold_file/test_bundle.json`

It created lineage
`2d297e0ebd5bfdb4a88e73528428c02173be2be13a09169e7db9a0b15fb5e2c8` with
`repair_mode=patch`, semantic depth two, the current patch schema and current
applier hash. `03-lineage-initialization.json` independently records the ten
controlled component references, the source prompt match and zero trial leaves
before V0. Initialization created no Provider calls.

## First live attempt and corrected fresh lineage

The first live V0 on lineage
`2d297e0ebd5bfdb4a88e73528428c02173be2be13a09169e7db9a0b15fb5e2c8` completed
provider calls for all three slots but was classified infrastructure-invalid
at report recomputation. Runtime correctly rejected a Qwen p1 patch because
the resulting draft failed the ArchitectureDraft Schema; the replay path did
not yet perform that post-application Schema check. The exact error and
hash-bound evidence are recorded in `04-v0-infrastructure-invalid.json`. That
lineage remains preserved and is not a complete assessment or prompt result.

The replay implementation was corrected to apply the same post-application
Schema check as runtime, deterministic calibration/reporting tests passed, and
the full pre-live gate was rerun before creating the active lineage
`a85f28d7f96f41cc7ef615c4bd03e95cbd8d1ed134f7d60894ecb4ce7ffbc415`. Its
reinitialization record is `05-lineage-reinitialization.json`; it started with
zero trial leaves and no Provider calls.

The active lineage completed one coherent V0 N=3 attempt. Recompute with
`--require-source-match` passed. All three reports are complete, each has
`infrastructure_invalid=false` and `truncations=0`, and screening is false:

| slot | p1 | p2 | semantic first pass | report sha256 |
| --- | ---: | ---: | ---: | --- |
| qwen | 0/3 | 0/3 | 0/3 | `0426ddd7510a28ef09de13faa03acea3e59a352cbb60bca1495b8d0ae3605216` |
| claude | 1/3 | 1/3 | 0/3 | `9c6ebbf30cf0597b4f9042bec900d101c6a38aebd7fa3db5679e04bf77788f45` |
| deepseek | 0/3 | 1/3 | 0/3 | `af6021c24d0c55346527c7dab0ca9449b01c8eef1a78a397ec6eb6c303bbc88e` |

The repeated final failures are concentrated in `arch_02`, `arch_06`,
`arch_08`, `arch_09`, `arch_10` and `arch_15`; raw error codes and all patch
repair/rejection/locality leaves remain under the active lineage. This is the
first complete valid evidence that can justify the single prompt-optimizer
review; it does not authorize a fallback, recovery, qualification or
production claim.

## V1 evidence after the single V0 revision

The V0 revision was admitted only after the complete failing V0 assessment.
The optimizer record is `06-architecture-optimizer-v1.md`; the V0 parent
prompt, failure evidence and new prompt hash are sealed in
`07-v0-prompt-archive.json`. The V1 revision input and its exact
parent/hash/diff binding are recorded in `08-v1-revision-input.json` and the
lineage's `prompt-development/versions/v1/revision.json`. The V1 prompt hash
is `0f074a933a8f90e54e98cd348acfa01121fe17dece3766f430714c56da8f3271`.

V1 completed one coherent N=3 attempt for all three slots. Recompute with
`--require-source-match` passed, every report has
`infrastructure_invalid=false`, all calls have `truncations=0`, and the
assessment is complete but `screening_pass=false`:

| slot | p1 | p2 | semantic first pass | report sha256 |
| --- | ---: | ---: | ---: | --- |
| qwen | 0/3 | 0/3 | 0/3 | `9759a62f892fd228a1f1c4ec74f8e55fe5ed36baa38381ada112fa623cfce2d9` |
| claude | 0/3 | 0/3 | 0/3 | `a21d0d2439e608b2213c0a1cd1905fbb7ba024188064ea9d338cb7f0d3b23934` |
| deepseek | 0/3 | 1/3 | 0/3 | `601f37466196500e2d9b0f3688ceaff022c8854696fd72e2602c4042334b7fe8` |

The V1 concrete-ledger hypothesis was supported for its declared diagnostic:
the initial `arch_02`/`arch_06`/`arch_09` failure counts fell from 8/9 per
gate in V0 to 2/9, 1/9 and 2/9 respectively, with improvement in Claude and
DeepSeek. It was not a quality-gate pass: remaining p2 failures and repeated
`ARCH_TEST_READINESS_UNCLOSED`, `ARCH_PATH_TOKEN_INVALID`,
`ARCH_DELIVERY_CONSTRAINT_VIOLATION`, `ARCH_MODULE_FILE_INVALID` and
`ARCH_LAYOUT_FROZEN_TASK_FILE` evidence require either a separately supported
prompt hypothesis or termination under the declared budget. No fallback,
recovery, qualification or production claim is authorized by this result.

## V2 admission decision and terminal blocker

After the complete, source-match-valid V1 attempt, the sole sequential prompt
optimizer review is recorded in `09-architecture-optimizer-v2.md` (SHA-256
`e4cfc40cbb772a210768e26b772555b1b47eb6771b33e31790e3ecc26f0797dc`). It
considered one distinct contract-backed test-readiness hypothesis but did not
admit V2 or modify the shared prompt. The prompt remains
`0f074a933a8f90e54e98cd348acfa01121fe17dece3766f430714c56da8f3271`.

The review classified the remaining blocker as validator/protocol rather than
prompt-fixable: the Schema/design-legal `{type_id}` layout form is tokenized
by the frozen validator as separate `type` and `id` tokens, so `arch_15` can
reject a legal draft; additionally, local `arch_15` repair paths can omit the
coupled ownership and work-package projections and cause `arch_02`/`arch_09`/
`arch_12` regressions. V1 is therefore the last admitted version in this
lineage. No V2 Provider I/O, validator change, protocol change, recovery
authorization, qualification, or production claim is made. Resolving this
blocker would require an owner-authorized design/protocol decision and a fresh
lineage; this change does not silently make that correction.

## Final audit snapshot

The final repository source hashes relevant to the active lineage are:

| artifact | sha256 |
| --- | --- |
| `nepa/calibration/s4_architecture.py` | `8b8c91626d2769e28b8526e0f6f35c687722c8b87a1a3980e6dec99284f6a96d` |
| `nepa/calibration/s4_prompt_development.py` | `ab0ace9605ae45905331695705c9873ceb45dbf6df217fccd04ae06f500e36c3` |
| `nepa/agents/prompts/architecture_planner.md` | `0f074a933a8f90e54e98cd348acfa01121fe17dece3766f430714c56da8f3271` |
| `nepa/schemas/architecture-patch.schema.json` | `dcfa37ed73d8db65e8ae4147e4f0ccc083c07e387036c7af833e7603dc8dc456` |
| `nepa/schemas/examples/architecture-patch.example.json` | `85759f019c07edb3a8aa26508ddee738b26089b2857b2cc0187ea2da3cf913fe` |

The active lineage V1 prompt snapshot is byte-identical to the repository
prompt. V0 `--require-source-match` recomputation passed before V1 admission;
an attempted final V0 source-match check after V1 admission correctly reports
repository prompt-source drift because V0 is an immutable historical snapshot.
V1 `--require-source-match` recomputation passed after V1 admission. The V0
snapshot remains unchanged, and no V2 trial, selection, handoff, fallback,
recovery, qualification or production artifact was created.

The owner-only terminal boundary remains recorded by the preregistration: a
future unique selection could only produce an M1-4a3 technical-admission
candidate, while a tie permits no handoff; this incomplete, blocker-terminated
run makes neither decision and does not authorize M1-4a2r, M1-4a3 qualification
or production use.
