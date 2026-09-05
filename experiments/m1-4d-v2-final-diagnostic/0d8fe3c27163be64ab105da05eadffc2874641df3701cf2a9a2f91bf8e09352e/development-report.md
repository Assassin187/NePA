# M1-4a2 development report

- Lineage: `0d8fe3c27163be64ab105da05eadffc2874641df3701cf2a9a2f91bf8e09352e`
- Terminal status: `diagnostic`
- Model slot: `architecture_primary`

## v0

- Trials per slot: `3`
- Screening pass: `False`
- Initial source: `prompt-development/versions/v0/initial.md`
- Repair source: `prompt-development/versions/v0/repair.md`
- Neutrality evidence: `prompt-development/versions/v0/neutrality.json`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| architecture_primary | 0.0 | 0.0 | 0.3333333333333333 | 1.0 | 0.0 | 0 | 0.0 | ["claude-opus-5"] |

### Slot diagnostics

- `architecture_primary`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_03", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

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
| arch_10 | 0.3333333333333333 |
| arch_11 | 1.0 |
| arch_12 | 1.0 |
| arch_13 | 1.0 |
| arch_14 | 1.0 |
| arch_15 | 1.0 |

## v1

- Trials per slot: `3`
- Screening pass: `False`
- Initial source: `prompt-development/versions/v1/initial.md`
- Repair source: `prompt-development/versions/v1/repair.md`
- Neutrality evidence: `prompt-development/versions/v1/neutrality.json`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| architecture_primary | 0.0 | 0.0 | 0.0 | 1.0 | 0.0 | 2 | 0.0 | ["claude-opus-5"] |

### Slot diagnostics

- `architecture_primary`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_03", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

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
| arch_08 | 0.6666666666666666 |
| arch_09 | 1.0 |
| arch_10 | 0.3333333333333333 |
| arch_11 | 1.0 |
| arch_12 | 1.0 |
| arch_13 | 1.0 |
| arch_14 | 1.0 |
| arch_15 | 1.0 |

## v2

- Trials per slot: `3`
- Screening pass: `False`
- Initial source: `prompt-development/versions/v2/initial.md`
- Repair source: `prompt-development/versions/v2/repair.md`
- Neutrality evidence: `prompt-development/versions/v2/neutrality.json`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| architecture_primary | 0.0 | 0.3333333333333333 | 0.3333333333333333 | 0.6666666666666666 | 0.0 | 1 | 0.0 | ["claude-opus-5"] |

### Slot diagnostics

- `architecture_primary`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_03", "arch_14", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

### Gate final pass rates

| gate | architecture_primary |
| --- | ---: |
| arch_01 | 0.3333333333333333 |
| arch_02 | 0.3333333333333333 |
| arch_03 | 0.3333333333333333 |
| arch_04 | 0.3333333333333333 |
| arch_05 | 0.3333333333333333 |
| arch_06 | 0.3333333333333333 |
| arch_07 | 0.3333333333333333 |
| arch_08 | 0.3333333333333333 |
| arch_09 | 0.3333333333333333 |
| arch_10 | 0.3333333333333333 |
| arch_11 | 0.3333333333333333 |
| arch_12 | 0.3333333333333333 |
| arch_13 | 0.3333333333333333 |
| arch_14 | 0.3333333333333333 |
| arch_15 | 0.3333333333333333 |

## Protocol exceptions

- `infrastructure_failed_sample_retry`: authorization=`explicit_user_authorization`, slot=`architecture_primary`, trial=`trial_001,trial_002,trial_003`, evidence=`prompt-development/versions/v0/attempts/attempt_002/declaration.json`

## Scope limitations

- This is only M1-4a2 N=3 baseline usability evidence.
- Long-term prompt quality is not proven here and must be observed during complete framework runs.
