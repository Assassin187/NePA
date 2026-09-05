# M1-4a2 development report

- Lineage: `4420296f4dead63a3119c49106e234147f4a69a1f4f4aecda8fa562af7944ab7`
- Terminal status: `selected`
- Model slot: `architecture_primary`

- Selected version: `v0`
- Selection reason: `first passing version`
- Selected bundle: `prompt-development/versions/v0/snapshot.json`
- M1-4c handoff: `prompt-development/handoff.json`

## v0

- Trials per slot: `3`
- Screening pass: `True`
- Initial source: `prompt-development/versions/v0/initial.md`
- Repair source: `prompt-development/versions/v0/repair.md`
- Neutrality evidence: `prompt-development/versions/v0/neutrality.json`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| architecture_primary | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1 | 0.0 | ["claude-opus-5"] |

### Slot diagnostics

- `architecture_primary`: infrastructure_invalid=`False`, repeated_initial_failures=`[]`, parameter_support=`{"temperature": ["unknown"]}`

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

## Scope limitations

- This is only M1-4a2 N=3 baseline usability evidence.
- Long-term prompt quality is not proven here and must be observed during complete framework runs.
