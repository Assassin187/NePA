# M1-4a2 development report

- Lineage: `0d8fe3c27163be64ab105da05eadffc2874641df3701cf2a9a2f91bf8e09352e`
- Terminal status: `not-ready`
- Model slot: `architecture_primary`

## v0

- Trials per slot: `3`
- Screening pass: `False`
- Initial source: `prompt-development/versions/v0/initial.md`
- Repair source: `prompt-development/versions/v0/repair.md`
- Neutrality evidence: `prompt-development/versions/v0/neutrality.json`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| architecture_primary | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0 | 0.0 | ["claude-opus-5", "unknown"] |

### Slot diagnostics

- `architecture_primary`: infrastructure_invalid=`True`, repeated_initial_failures=`["arch_01", "arch_02", "arch_03", "arch_04", "arch_05", "arch_06", "arch_07", "arch_08", "arch_09", "arch_10", "arch_11", "arch_12", "arch_13", "arch_14", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

### Gate final pass rates

| gate | architecture_primary |
| --- | ---: |
| arch_01 | 0.0 |
| arch_02 | 0.0 |
| arch_03 | 0.0 |
| arch_04 | 0.0 |
| arch_05 | 0.0 |
| arch_06 | 0.0 |
| arch_07 | 0.0 |
| arch_08 | 0.0 |
| arch_09 | 0.0 |
| arch_10 | 0.0 |
| arch_11 | 0.0 |
| arch_12 | 0.0 |
| arch_13 | 0.0 |
| arch_14 | 0.0 |
| arch_15 | 0.0 |

## Scope limitations

- This is only M1-4a2 N=3 baseline usability evidence.
- Long-term prompt quality is not proven here and must be observed during complete framework runs.
