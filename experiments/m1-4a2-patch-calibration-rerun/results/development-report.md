# M1-4a2 development report

- Lineage: `ee5a23a8fcbaa5dc273f36c0365707fac5a9684f050463fc32ec7fd6bc3b67a5`
- Terminal status: `selected`
- Model slot: `architecture_primary`

- Selected version: `v1`
- Selection reason: `first passing version after authorized infrastructure-failed sample retry`
- Selected bundle: `prompt-development/versions/v1/snapshot.json`
- M1-4c handoff: `prompt-development/handoff.json`

## v0

- Trials per slot: `3`
- Screening pass: `False`
- Initial source: `prompt-development/versions/v0/initial.md`
- Repair source: `prompt-development/versions/v0/repair.md`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| architecture_primary | 0.0 | 0.0 | 0.0 | 1.0 | 0.0 | 3 | 0.0 | ["claude-opus-5", "unknown"] |

### Slot diagnostics

- `architecture_primary`: infrastructure_invalid=`True`, repeated_initial_failures=`["arch_14", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

### Gate final pass rates

| gate | architecture_primary |
| --- | ---: |
| arch_01 | 0.3333333333333333 |
| arch_02 | 0.3333333333333333 |
| arch_03 | 0.3333333333333333 |
| arch_04 | 0.3333333333333333 |
| arch_05 | 0.3333333333333333 |
| arch_06 | 0.3333333333333333 |
| arch_07 | 0.0 |
| arch_08 | 0.3333333333333333 |
| arch_09 | 0.3333333333333333 |
| arch_10 | 0.0 |
| arch_11 | 0.3333333333333333 |
| arch_12 | 0.3333333333333333 |
| arch_13 | 0.3333333333333333 |
| arch_14 | 0.3333333333333333 |
| arch_15 | 0.3333333333333333 |

## v1

- Trials per slot: `3`
- Screening pass: `True`
- Initial source: `prompt-development/versions/v1/initial.md`
- Repair source: `prompt-development/versions/v1/repair.md`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| architecture_primary | 0.0 | 0.6666666666666666 | 1.0 | 1.0 | 0.0 | 0 | 0.0 | ["claude-opus-5"] |

### Slot diagnostics

- `architecture_primary`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

### Gate final pass rates

| gate | architecture_primary |
| --- | ---: |
| arch_01 | 1.0 |
| arch_02 | 1.0 |
| arch_03 | 1.0 |
| arch_04 | 1.0 |
| arch_05 | 1.0 |
| arch_06 | 1.0 |
| arch_07 | 1.0 |
| arch_08 | 1.0 |
| arch_09 | 1.0 |
| arch_10 | 1.0 |
| arch_11 | 1.0 |
| arch_12 | 1.0 |
| arch_13 | 1.0 |
| arch_14 | 1.0 |
| arch_15 | 1.0 |

## Protocol exceptions

- `infrastructure_failed_sample_retry`: authorization=`explicit_user_authorization`, slot=`architecture_primary`, trial=`trial_001,trial_002,trial_003`, evidence=`prompt-development/versions/v1/attempts/attempt_002/declaration.json`
- `infrastructure_failed_sample_retry`: authorization=`explicit_user_authorization`, slot=`architecture_primary`, trial=`trial_001,trial_002,trial_003`, evidence=`prompt-development/versions/v1/attempts/attempt_003/declaration.json`

## Scope limitations

- This is only M1-4a2 N=3 baseline usability evidence.
- Long-term prompt quality is not proven here and must be observed during complete framework runs.
