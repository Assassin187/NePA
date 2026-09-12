# M1-13 natural-failure root-cause report

Generated deterministically from the frozen protocol, manifest, and referenced Run artifacts.

## Sample and classification summary

| measure | value |
| --- | ---: |
| admitted_runs | 5 |
| excluded_runs | 2 |
| natural_failure_tasks | 0 |
| classified_tasks | 0 |
| indeterminate_tasks | 0 |
| planning_defects | 0 |
| local_implementation_defects | 0 |

## Real natural-failure samples

| run_id | task_uid | classification |
| --- | --- | --- |
| — | — | no natural failure samples |

## Admitted run outcomes

| run_id | termination | S4 status | S4 error |
| --- | --- | --- | --- |
| 20260911T083325Z_MQTT_spec-run | controlled_exit | failed | structured output failed Schema validation |
| 20260911T084237Z_MQTT_spec-run | controlled_exit | failed | structured output contains no complete JSON value |
| 20260911T085312Z_MQTT_spec-run | controlled_exit | failed | structured output contains no complete JSON value |
| 20260911T090028Z_MQTT_spec-run | controlled_exit | failed | structured output contains no complete JSON value |
| 20260911T090748Z_MQTT_spec-run | controlled_exit | failed | structured output contains no complete JSON value |

## Separate synthetic mechanism evidence

| id | description |
| --- | --- |
| — | no synthetic rows declared |

## Returned model identity disclosure

| observed identity | call count |
| --- | ---: |
| deepseek-v4-pro | 5 |

## Parameter and production conclusion

D1.14 established: `false`.
Production enablement established: `false`.

## Cost reference and limitations

Admitted-run telemetry: 245600 input tokens, 160000 output tokens, and $0.302333 recorded USD cost.
Costs are operational references only and do not affect admission, classification, parameter selection, or acceptance. USD telemetry uses the frozen runtime prices; CNY source prices and the fixed ¥7.2/USD accounting conversion remain disclosed in the machine result. Claude cached-input is $0.5/1M but is not applied without trustworthy cached-token counts; NePA local cache hits remain zero incremental cost.

- N=5 is descriptive; no p-values or significance claims are made.
- Task samples within a Run are correlated.
- Provider cached-input cost is not recomputed without cached-token evidence.
