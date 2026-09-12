# Protocol expansion implementation and evidence

The approved next iteration keeps all 110 original MQTT requirements, adds independent
core behavior checks, compares JSON-object with native tool calls under strict local
validation, and requires one fresh success for MQTT and the HTTP fixed-length subset.
No OpenSpec workflow is used. Inputs are parallel under gold_file/mqtt and gold_file/http;
each has specIR.json, target.json, acceptance.json and read-only oracle assets.

## Budgets and experiment order

1. Offline tests and historical export-copy audit.
2. Freeze and execute action_study.py: 24 Flash/8 Pro paired samples per interface,
   four actual tool fixtures per interface, strict Beta capability probe. CNY10 maximum,
   included in the new MQTT campaign. Native promotion follows preregistered gates.
3. Freeze the selected candidate. Launch empty-project MQTT and HTTP generations
   concurrently, as subsequently authorized by the user. Independently rebuild and
   verify each export; one failure does not stop the other experiment. These are
   feasibility samples, not stability.

Latest budget authorization: use new roots runs/mqtt-e2e and runs/http-e2e, each
CNY300 cumulative, each generation CNY20/four hours. Study total CNY10 is included
in the new MQTT campaign and is not replenished on retry. These replace earlier USD
limits for this iteration. Historic runs remain under runs/_refactor/worktree/runs/e2e
and are explicitly excluded from the new limits. All new failures/reservations count.

Domestic prices are captured from the official Chinese price page on 2026-09-13:
Flash peak cache-hit input / miss input / output = CNY0.04/2/8 per million tokens;
Pro = CNY0.30/9/27. Off-peak is half price. Peak is Asia/Shanghai Mon-Fri 09:00-12:00
and 14:00-18:00. Run6.0 records request-start UTC, selected period/rates and available
cache counts. Missing cache counts assume all misses; unknown usage retains the
peak-price reservation. These are estimates, not invoices. Legacy USD reports stay
unchanged and require their original runtime.

## Historical audit

The original deliveries and reports were not changed. Copies were clean-built for
release and ASan/UBSan and run through 20 mandatory checks (minimum plus 19 additions).
Raw results and per-requirement scenario joins: runs/behavior-audit-v4/summary.json.

| Historical run | Build variants | Expanded checks |
|---|---|---|
| 20260912T112242Z-56f67d18 | Both passed | All passed |
| 20260912T135449Z-4c036768 | Both passed | 12 cases failed in each variant |
| 20260912T135449Z-22021a32 | Both passed | 6 cases failed in each variant |

4c036768 failures: pubsub, multi_client, unsubscribe, session_isolation, qos,
session_reset, fragmented_publish, coalesced, length_boundaries, truncated,
invalid_flags, invalid_utf8. 22021a32 failures: session_reset, duplicate_connect,
invalid_qos, fragmented_publish, coalesced, invalid_flags. This does not revoke the
historical minimum-check result; it exposes behavior that it did not verify.
An initial audit attempt retained its build/check output but its summary publication
failed on relative-path handling; v2 reran after that harness fix. The v3 audit
freezes its own input assets and accepts TCP reset as connection closure where the
scenario only requires closure. Both later audits found the same failing cases.
The final v4 audit narrows the requirement mapping (the underlying transport
assumption remains unverified); failing scenarios are unchanged. Original input/source
hashes were unchanged for all three deliveries.

## Current implementation

The final mapping links 51 MQTT requirements to actual server assertions and leaves
59 explicit gaps. In particular, stream scenarios do not prove the background
assumption that the underlying transport is lossless.

Report4.0 lists all requirements and keeps claims separate from final-export scenario
outcomes. Optional checks and missing/incomplete evidence never establish verification.
Config2.0 uses coder.action_format (json_object/tool_calls); default selection remains
JSON until the real paired study supports changing it. Native calls retain streaming
arguments, IDs and reasoning content; the unchanged local action schema gates execution.

HTTP has 27 manually curated requirements and 12 mandatory cases. Its target is
byte-identical to MQTT's C99/server target. The local profile distinguishes selected
RFC9110/9112 rules from application decisions. Chunked is deliberately excluded, so
this is not full HTTP/1.1 conformance. No HTTP-specific generator path was added.

Offline verification passed 172 non-paid tests, Ruff, mypy and sdist/wheel builds.
## Completed action-interface comparison

Frozen study candidate: 73cdc4b. Preregistration and complete raw results are under
runs/action-study-cny-v1; individual calls/fixtures remain in runs/mqtt-e2e and are
marked as study-only runs, never protocol-generation successes. All 64 format samples
(24 Flash and 8 Pro per mode), eight short sessions and strict Beta probe completed.

| Metric | JSON-object | Native tool calls |
|---|---:|---:|
| Flash invalid | 15/24 (62.5%) | 22/24 (91.7%) |
| Pro invalid | 0/8 | 4/8 |
| Format-call seconds per valid action | 3.843 | 13.457 |
| Format-call CNY per valid action | 0.01069 | 0.08291 |
| Invalid format generation time | 21.693 s | 58.902 s |
| Actual short sessions passed | 3/4 | 3/4 |

JSON failures: 11 XML/DSML and four syntax/prose responses. All 26 native failures
were multiple calls; none executed. Read/write, replacement and finish sessions passed
in both modes. Compiler-repair sessions built successfully but expanded the tiny fixture
into a persistent server and timed out in the predeclared execution check. Those fixtures
retain server-task context, so these failures alone do not measure pure syntax-repair
ability. No thresholds or fixture checks were changed after seeing results.

Strict Beta returned HTTP400: required properties must match all object properties.
The unchanged AgentAction schemas include optional properties; the probe did not
rewrite them or weaken local validation. Its unknown usage keeps a CNY0.145658 peak
reservation. Total study ledger cost is CNY1.16708254 (settled estimate CNY1.02142454
plus that reservation), within the fixed CNY10 sublimit. All settled calls used off-peak
rates and available provider cache usage. No response cache or generated protocol is
claimed by this study. The default remains JSON-object: promotion gates failed.

New protocol generations have not yet run. Record their independent final-export
results here before claiming this iteration complete.
