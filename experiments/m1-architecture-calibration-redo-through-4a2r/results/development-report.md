# M1-4a2 development report

- Lineage: `966954e7865b97339a123d30f558fe882a09bac8d00dcf30ead1ea2edae434bf`
- Terminal status: `selected`
- Recovery: `not_triggered`

- Selected version: `v1`
- Selection reason: `unique fixed fallback winner after authorized single-slot Claude retry exception`
- M1-4a3 handoff: `prompt-development/handoff.json`

## v0

- Trials per slot: `5`
- Screening pass: `False`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| qwen | 0.0 | 0.0 | None | 1.0 | 0.0 | 0 | 0.0 | ["qwen3.7-max-2026-06-08"] |
| claude | 0.0 | 0.8 | None | 1.0 | 0.0 | 0 | 0.0 | ["claude-opus-5"] |
| deepseek | 0.0 | 0.0 | None | 1.0 | 0.0 | 0 | 0.0 | ["deepseek-v4-flash"] |

### Slot diagnostics

- `qwen`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_02", "arch_03", "arch_04", "arch_05", "arch_07", "arch_08", "arch_09", "arch_12", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`
- `claude`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_02", "arch_03", "arch_06", "arch_09", "arch_10", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`
- `deepseek`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_02", "arch_03", "arch_04", "arch_06", "arch_07", "arch_09", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

### Gate final pass rates

| gate | qwen | claude | deepseek |
| --- | ---: | ---: | ---: |
| arch_01 | 1.0 | 1.0 | 1.0 |
| arch_02 | 0.6 | 1.0 | 1.0 |
| arch_03 | 0.2 | 0.8 | 0.4 |
| arch_04 | 0.6 | 0.8 | 0.4 |
| arch_05 | 0.8 | 1.0 | 1.0 |
| arch_06 | 1.0 | 1.0 | 1.0 |
| arch_07 | 0.6 | 0.8 | 0.4 |
| arch_08 | 0.6 | 1.0 | 0.4 |
| arch_09 | 0.6 | 1.0 | 1.0 |
| arch_10 | 0.8 | 0.8 | 0.8 |
| arch_11 | 1.0 | 1.0 | 1.0 |
| arch_12 | 0.6 | 1.0 | 0.8 |
| arch_13 | 1.0 | 1.0 | 1.0 |
| arch_14 | 1.0 | 1.0 | 1.0 |
| arch_15 | 0.4 | 1.0 | 0.8 |

## v1

- Trials per slot: `5`
- Screening pass: `False`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| qwen | 0.0 | 0.2 | None | 1.0 | 0.0 | 0 | 0.0 | ["qwen3.7-max-2026-06-08"] |
| claude | 0.0 | 0.6 | None | 1.0 | 0.0 | 0 | 0.0 | ["claude-opus-5"] |
| deepseek | 0.0 | 0.2 | None | 1.0 | 0.0 | 0 | 0.0 | ["deepseek-v4-flash"] |

### Slot diagnostics

- `qwen`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_02", "arch_06", "arch_08", "arch_09", "arch_10", "arch_12", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`
- `claude`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_01", "arch_02", "arch_03", "arch_06", "arch_09", "arch_10", "arch_12", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`
- `deepseek`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_02", "arch_06", "arch_09", "arch_10", "arch_12", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

### Gate final pass rates

| gate | qwen | claude | deepseek |
| --- | ---: | ---: | ---: |
| arch_01 | 1.0 | 1.0 | 1.0 |
| arch_02 | 0.8 | 1.0 | 0.8 |
| arch_03 | 1.0 | 1.0 | 1.0 |
| arch_04 | 1.0 | 1.0 | 1.0 |
| arch_05 | 0.8 | 1.0 | 1.0 |
| arch_06 | 1.0 | 1.0 | 0.8 |
| arch_07 | 1.0 | 1.0 | 1.0 |
| arch_08 | 0.4 | 1.0 | 0.8 |
| arch_09 | 0.8 | 1.0 | 0.8 |
| arch_10 | 0.8 | 1.0 | 0.6 |
| arch_11 | 1.0 | 1.0 | 1.0 |
| arch_12 | 0.8 | 1.0 | 0.6 |
| arch_13 | 0.8 | 1.0 | 1.0 |
| arch_14 | 1.0 | 1.0 | 1.0 |
| arch_15 | 0.8 | 0.6 | 0.8 |

## v2

- Trials per slot: `10`
- Screening pass: `False`

| slot | p0 | p1 | p2 | schema-after-format | semantic-first | truncated | cost_usd | model strings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| qwen | 0.0 | 0.3 | None | 1.0 | 0.0 | 0 | 0.0 | ["qwen3.7-max-2026-06-08"] |
| claude | 0.0 | 0.5 | None | 1.0 | 0.0 | 1 | 0.0 | ["claude-opus-5"] |
| deepseek | 0.0 | 0.2 | None | 0.9 | 0.0 | 0 | 0.0 | ["deepseek-v4-flash"] |

### Slot diagnostics

- `qwen`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_02", "arch_05", "arch_06", "arch_07", "arch_08", "arch_09", "arch_10", "arch_12", "arch_13", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`
- `claude`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_01", "arch_02", "arch_03", "arch_06", "arch_09", "arch_10", "arch_12", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`
- `deepseek`: infrastructure_invalid=`False`, repeated_initial_failures=`["arch_01", "arch_02", "arch_06", "arch_08", "arch_09", "arch_10", "arch_11", "arch_12", "arch_14", "arch_15"]`, parameter_support=`{"temperature": ["unknown"]}`

### Gate final pass rates

| gate | qwen | claude | deepseek |
| --- | ---: | ---: | ---: |
| arch_01 | 0.9 | 1.0 | 0.9 |
| arch_02 | 0.8 | 1.0 | 0.8 |
| arch_03 | 0.8 | 1.0 | 0.9 |
| arch_04 | 1.0 | 1.0 | 0.9 |
| arch_05 | 1.0 | 1.0 | 0.9 |
| arch_06 | 1.0 | 1.0 | 0.8 |
| arch_07 | 1.0 | 1.0 | 0.9 |
| arch_08 | 0.7 | 1.0 | 0.9 |
| arch_09 | 0.8 | 1.0 | 0.8 |
| arch_10 | 0.8 | 1.0 | 0.7 |
| arch_11 | 1.0 | 1.0 | 0.9 |
| arch_12 | 0.8 | 1.0 | 0.7 |
| arch_13 | 1.0 | 1.0 | 0.9 |
| arch_14 | 1.0 | 0.9 | 0.5 |
| arch_15 | 0.7 | 0.6 | 0.6 |

## Protocol exceptions

- `single_slot_retry`: authorization=`explicit_user_authorization`, slot=`claude`, trial=`trial_010`, evidence=`v2/extensions/n010/slot-retry-001/exception.json`

## Scope limitations

- This is M1-4a2 development screening evidence, not M1-4a3 N=10 qualification.
- No B1-B4, production model, call-shape, budget, formal Run, S4, S5, S6, or production-freeze claim is made.
