# NePA

Generate a protocol project from manually curated Spec (including requirements),
target format and independent acceptance assets. Initial scope: Linux x86_64/C99
servers, with MQTT as the first evaluation input, not a generator special case.

See project_docs/system_design.md for the approved contract and
project_docs/refactor_plan.md for actual implementation/acceptance status.

Validated2026-09-12: one real development baseline, then two fresh optimized-candidate
runs passed the defined builds and minimum interactions. The repeated runs took
76.8/75.4 minutes versus128.8 baseline minutes excluding its recharge pause.
This is not full MQTT conformance or three unchanged-candidate runs. Detailed
evidence and limitations: project_docs/refactor_plan.md and
project_docs/session_latency_analysis.md.

Validated2026-09-13: one fresh MQTT generation and one fresh HTTP fixed-length-subset
generation passed expanded mandatory scenarios and independent export rebuilds in
both release and san variants. The 110 MQTT requirements remain intact, with 51 mapped
scenario passes and 59 explicit gaps. Action-interface comparison did not support
switching away from JSON. Current domestic-CNY costs, archives and limitations:
[protocol expansion results](project_docs/protocol_expansion.md).

## Development

```bash
uv sync --extra dev
docker build -t nepa-sandbox:refactor -f docker/sandbox.Dockerfile .
uv run pytest -q -m "not live_e2e"
uv run ruff check nepa tests
uv run mypy nepa
```

## Generation: new CLI contract

Set the API-key environment variable named by your provider configuration; never
write credentials into committed configuration or a generated project.

```bash
uv run nepa run --spec gold_file/mqtt/specIR.json --target gold_file/mqtt/target.json \
  --acceptance gold_file/mqtt/acceptance.json --config configs/default.yaml --runs-root runs/mqtt-e2e
uv run nepa status RUN_ID --runs-root runs/mqtt-e2e
uv run nepa resume RUN_ID --runs-root runs/mqtt-e2e
```

The supplied config uses V4.1 Flash (`deepseek-flash`) for initial ordinary coding
sessions and V4 Pro for wire/integration and retry/repair sessions. Costs use domestic CNY rates and the Asia/Shanghai busy/off-peak schedule.
Responses record cache usage when provided; missing cache counts assume misses.
Unknown calls retain peak-price reservations. These are estimates, not invoices.
See configs/default.yaml and project_docs/protocol_expansion.md.

An explicitly approved development continuation can change its active configuration:

```bash
uv run nepa resume RUN_ID --runs-root runs/mqtt-e2e --config configs/default.yaml \
  --accept-runtime-change --change-reason "Describe the authorized experiment change"
```

This preserves previous state/report evidence, costs, attempts and the original
deadline. Ordinary resume rejects runtime drift. A mixed-version development run
must not be presented as an unchanged-candidate stability sample.

Successful exports contain sources, Makefile, README and release/san executables.
Build without NePA using make clean then make release san. Exit zero requires all
tasks, mandatory checks and published artifacts. Claims are not verified behavior.

Manual input sets are parallel: `gold_file/mqtt/` and `gold_file/http/`, each with
`specIR.json`, `target.json`, `acceptance.json` and independent oracle scripts.
MQTT retains 110 requirements and now has 20 core-behavior checks. HTTP contains
27 manually curated fixed-length-subset requirements and 12 checks. Both use the
same C99/server target. These checks do not establish full protocol conformance.
Report4.0 joins every claim to actual final-export scenario results or explicit gaps.
Config2.0 selects `coder.action_format: json_object` or `tool_calls`; local action
validation stays strict in both modes. Old runs require their original runtime.

```bash
uv run nepa run --spec gold_file/http/specIR.json --target gold_file/http/target.json \
  --acceptance gold_file/http/acceptance.json --config configs/default.yaml --runs-root runs/http-e2e
NEPA_LIVE_E2E=1 uv run pytest -s -q -m live_e2e tests/test_live_e2e.py
```

The opt-in paid harness runs fresh MQTT and HTTP generations concurrently after
freezing both input sets and the same candidate, then independently checks each export.
MQTT uses the new `runs/mqtt-e2e` CNY campaign; HTTP uses `runs/http-e2e`.
Each has a ¥300 cumulative ceiling, including new failures and reservations;
each generation is capped at ¥20/four hours. The interface study has a fixed ¥10
sublimit within the new MQTT campaign. The user explicitly excluded old USD runs
from these new ceilings; old evidence remains in its original historical root.
Run6.0 and Config2.0 reject currency mixing. Evidence and remaining work:
`project_docs/protocol_expansion.md`.
