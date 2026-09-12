# NePA

Generate a protocol project from manually curated Spec (including requirements),
target format and independent acceptance assets. Initial scope: Linux x86_64/C99
servers, with MQTT as the first evaluation input, not a generator special case.

See project_docs/system_design.md for the approved contract and
project_docs/refactor_plan.md for actual implementation/acceptance status.

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
uv run nepa run --spec gold_file/specIR.json --target gold_file/target.json \
  --acceptance gold_file/acceptance.json --config configs/default.yaml --runs-root runs/e2e
uv run nepa status RUN_ID --runs-root runs/e2e
uv run nepa resume RUN_ID --runs-root runs/e2e
```

The supplied config uses V4.1 Flash (`deepseek-flash`) for initial ordinary coding
sessions and V4 Pro for wire/integration and retry/repair sessions. Token costs are
conservative peak, cache-miss estimates, not the provider invoice. See the pricing
source and rates in configs/default.yaml and project_docs/refactor_plan.md.

An explicitly approved development continuation can change its active configuration:

```bash
uv run nepa resume RUN_ID --runs-root runs/e2e --config configs/default.yaml \
  --accept-runtime-change --change-reason "Describe the authorized experiment change"
```

This preserves previous state/report evidence, costs, attempts and the original
deadline. Ordinary resume rejects runtime drift. A mixed-version development run
must not be presented as an unchanged-candidate stability sample.

Successful exports contain sources, Makefile, README and release/san executables.
Build without NePA using make clean then make release san. Exit zero requires all
tasks, mandatory checks and published artifacts. Claims are not verified behavior.

The minimum MQTT oracle covers CONNECT, PING and a refusal path; it does not prove
all requirements, full MQTT conformance, other protocols or other languages. Paid
live tests are opt-in. Old run versions require their original code for reproduction.

```bash
NEPA_LIVE_E2E=1 uv run pytest -s -q -m live_e2e tests/test_live_e2e.py
```

The paid harness first requires one complete generation and independent export
verification. Only then does it launch two independent repetitions in parallel on
the same frozen candidate. Current authorized ceilings: $100 per run, $300 total
including prior failed/debug runs, and four hours per run.
For the explicitly authorized continuation workflow, set NEPA_LIVE_FIRST_RUN to
its successful run ID. The harness rechecks its export before starting two fresh
repetitions and explicitly records the first run's configuration changes.
